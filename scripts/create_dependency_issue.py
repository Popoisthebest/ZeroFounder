from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_client import GitHubClient
from agents.schemas import ActionEnvelope, ActionType

MARKER = "<!-- zerofounder-dependency-proposal -->"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", type=Path, required=True)
    args = parser.parse_args()
    action = ActionEnvelope.model_validate_json(args.action.read_text())
    is_dependency = action.action_type == ActionType.PROPOSE_DEPENDENCY
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"created={str(is_dependency).lower()}\n")
    if not is_dependency or not action.dependency_proposal:
        return 0
    proposal = action.dependency_proposal
    body = (
        f"{action.summary}\n\n{action.rationale}\n\n"
        "A verified founder must review the exact package, version, license, security, and "
        "maintenance impact. Approve with an exact `/approve` comment.\n\n"
        f"{MARKER}\n```json\n{proposal.model_dump_json(indent=2)}\n```\n"
    )
    GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]).create_issue(
        f"Dependency approval: {proposal.package_name}@{proposal.exact_version}",
        body,
        ["tool-request", "requires-approval", "agent-generated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
