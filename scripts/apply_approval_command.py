from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agents.approval import apply_command, decide_command
from agents.schemas import CompanyState, RepositoryCheckpoint


def run(command: list[str], root: Path) -> None:
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        raise SystemExit(f"approval git operation failed: {' '.join(command[:2])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    payload = json.loads(args.input.read_text())
    if payload.get("valid") is not True:
        return 0
    state_path = root / "company/state.json"
    state = CompanyState.model_validate_json(state_path.read_text())
    updated = apply_command(state, decide_command(state, payload["command"]))
    updated.last_agent_run = datetime.now(UTC)
    state_path.write_text(updated.model_dump_json(indent=2) + "\n")
    checkpoint_path = root / "company/checkpoints.json"
    checkpoint = RepositoryCheckpoint.model_validate_json(checkpoint_path.read_text())
    checkpoint.processed_comment_ids = sorted(
        set(checkpoint.processed_comment_ids + [int(payload["comment_id"])])
    )[-5000:]
    checkpoint.idempotency_keys = (
        checkpoint.idempotency_keys + [f"approval-comment:{int(payload['comment_id'])}"]
    )[-1000:]
    checkpoint.updated_at = datetime.now(UTC)
    checkpoint_path.write_text(checkpoint.model_dump_json(indent=2) + "\n")
    run(["git", "config", "user.name", "github-actions[bot]"], root)
    run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        root,
    )
    run(["git", "add", "company/state.json", "company/checkpoints.json"], root)
    message = f"chore(agent): approval-{payload['command']} [run:{os.environ['GITHUB_RUN_ID']}]"
    run(["git", "commit", "-m", message], root)
    run(["git", "push", "origin", "HEAD"], root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
