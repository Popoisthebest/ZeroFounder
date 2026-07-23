from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from agents.operating_output import action_commit_message
from agents.report_materializer import report_artifact_path, report_period
from agents.safety import SafetyViolation, validate_action_files
from agents.schemas import (
    ActionEnvelope,
    ActionType,
    CompanyState,
    MaterializedActionEnvelope,
)


def agent_branch_name(action: ActionEnvelope | MaterializedActionEnvelope, run_id: str) -> str:
    if not re.fullmatch(r"[0-9]{1,30}", run_id):
        raise ValueError("invalid run id")
    return f"agent/{run_id}-{action.action_type.value.replace('_', '-')}"


def run(command: list[str], root: Path, *, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=root, text=True, capture_output=capture, check=False)
    if result.returncode:
        raise SystemExit(f"git operation failed: {' '.join(command[:2])}")
    return result.stdout.strip() if capture else ""


def output(name: str, value: str) -> None:
    destination = os.environ["GITHUB_OUTPUT"]
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def validate_materialized_action_for_commit(
    action: MaterializedActionEnvelope,
    root: Path,
) -> None:
    try:
        validate_action_files(action, workspace=root)
    except SafetyViolation as exc:
        raise ValueError(str(exc)) from exc
    if any(change.path == "company/checkpoints.json" for change in action.files):
        raise ValueError("checkpoint cannot be provided by materialized action files")
    if action.action_type == ActionType.CREATE_IDEA_CANDIDATES:
        if action.state_transition is not None:
            raise ValueError("create_idea_candidates cannot change lifecycle stage")
        state = CompanyState.model_validate_json((root / "company/state.json").read_text())
        if not state.active_problem_id:
            raise ValueError("active_problem_id is required for idea candidate commit")
        expected = f"research/ideas/{state.active_problem_id}.json"
        paths = [change.path for change in action.files]
        if paths != [expected]:
            raise ValueError("create_idea_candidates materialized file path is not allowed")
    if action.action_type == ActionType.WRITE_REPORT:
        expected = report_artifact_path(report_period(root))
        paths = [change.path for change in action.files]
        if action.state_transition is not None:
            raise ValueError("write_report cannot change lifecycle stage")
        if paths != [expected]:
            raise ValueError("write_report materialized file path is not allowed")
        if not action.files[0].content.startswith("%PDF-") or len(action.files[0].content) < 20:
            raise ValueError("write_report materialized file must be a non-empty PDF")


def commit_agent_changes(root: Path, action_path: Path, run_id: str) -> tuple[bool, str, str]:
    try:
        action = MaterializedActionEnvelope.model_validate_json(action_path.read_text())
        branch = agent_branch_name(action, run_id)
        validate_materialized_action_for_commit(action, root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    run(["git", "config", "user.name", "github-actions[bot]"], root)
    run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        root,
    )
    current_branch = run(["git", "branch", "--show-current"], root, capture=True)
    if current_branch != branch:
        raise SystemExit("agent changes must be committed from the prepared agent branch")
    paths = [change.path for change in action.files]
    if action.state_transition:
        paths.append("company/state.json")
    paths.append("company/checkpoints.json")
    run(["git", "add", "--", *sorted(set(paths))], root)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root, check=False)
    if staged.returncode == 0:
        return False, branch, ""
    message = action_commit_message(action, run_id)
    run(["git", "commit", "-m", message], root)
    run(["git", "push", "--set-upstream", "origin", branch], root)
    sha = run(["git", "rev-parse", "HEAD"], root, capture=True)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("git returned an invalid commit SHA")
    return True, branch, sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    changed, branch, sha = commit_agent_changes(root, args.action, args.run_id)
    if not changed:
        output("changed", "false")
        return 0
    output("changed", "true")
    output("branch", branch)
    output("sha", sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
