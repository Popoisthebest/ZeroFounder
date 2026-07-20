from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from agents.dependencies import apply_approved_dependency
from agents.schemas import DependencyProposal


def run(command: list[str], root: Path, *, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=root, text=True, capture_output=capture, check=False)
    if result.returncode:
        raise SystemExit(f"dependency command failed: {' '.join(command[:2])}")
    return result.stdout.strip() if capture else ""


def output(name: str, value: str) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    if payload.get("valid") is not True or payload.get("kind") != "dependency":
        return 0
    proposal = DependencyProposal.model_validate(payload["dependency_proposal"])
    root = args.root.resolve()
    apply_approved_dependency(
        root,
        proposal,
        approved_by=str(payload["actor"]),
        has_write_permission=True,
    )
    run(["python", "-m", "pytest"], root)
    run(["ruff", "check", "agents", "tests", "scripts"], root)
    run(
        [
            "python",
            "-m",
            "pip_audit",
            "--cache-dir",
            ".cache/pip-audit",
            "-r",
            "requirements.txt",
        ],
        root,
    )
    if (root / "package-lock.json").exists():
        run(["npm", "audit", "--audit-level=high"], root)
        run(["npm", "run", "lint"], root)
        run(["npm", "run", "typecheck"], root)
        run(["npm", "test", "--", "--run"], root)
        run(["npm", "run", "build"], root)
    branch = f"dependency/{proposal.proposal_id}"
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
    run(["git", "checkout", "-b", branch], root)
    files = (
        ["requirements.txt"]
        if proposal.ecosystem == "python"
        else ["package.json", "package-lock.json"]
    )
    run(["git", "add", "--", *files], root)
    run(
        ["git", "commit", "-m", f"chore(deps): approve {proposal.proposal_id} [run:{args.run_id}]"],
        root,
    )
    run(["git", "push", "--set-upstream", "origin", branch], root)
    sha = run(["git", "rev-parse", "HEAD"], root, capture=True)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("invalid dependency commit SHA")
    output("branch", branch)
    output("sha", sha)
    output("proposal_id", proposal.proposal_id)
    output("package", f"{proposal.package_name}@{proposal.exact_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
