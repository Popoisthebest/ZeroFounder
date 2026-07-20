from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from agents.schemas import ActionEnvelope
from scripts.commit_agent_changes import agent_branch_name


def create_agent_branch(root: Path, action_path: Path, run_id: str) -> str:
    action = ActionEnvelope.model_validate_json(action_path.read_text())
    branch = agent_branch_name(action, run_id)
    result = subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=root,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("could not create the local agent branch")
    return branch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        create_agent_branch(args.root.resolve(), args.action, args.run_id)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
