from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from agents.schemas import ActionEnvelope


def run(command: list[str], root: Path, *, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=root, text=True, capture_output=capture, check=False)
    if result.returncode:
        raise SystemExit(f"git operation failed: {' '.join(command[:2])}")
    return result.stdout.strip() if capture else ""


def output(name: str, value: str) -> None:
    destination = os.environ["GITHUB_OUTPUT"]
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{1,30}", args.run_id):
        raise SystemExit("invalid run id")
    root = args.root.resolve()
    action = ActionEnvelope.model_validate_json(args.action.read_text())
    branch = f"agent/{args.run_id}-{action.action_type.value.replace('_', '-')}"
    run(["git", "config", "user.name", "github-actions[bot]"], root)
    run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        root,
    )
    run(["git", "checkout", "-b", branch], root)
    paths = [change.path for change in action.files]
    if action.state_transition:
        paths.append("company/state.json")
    paths.append("company/checkpoints.json")
    run(["git", "add", "--", *sorted(set(paths))], root)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root, check=False)
    if staged.returncode == 0:
        output("changed", "false")
        return 0
    message = f"chore(agent): {action.action_type.value} [run:{args.run_id}]"
    run(["git", "commit", "-m", message], root)
    run(["git", "push", "--set-upstream", "origin", branch], root)
    sha = run(["git", "rev-parse", "HEAD"], root, capture=True)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("git returned an invalid commit SHA")
    output("changed", "true")
    output("branch", branch)
    output("sha", sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
