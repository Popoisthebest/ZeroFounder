from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_client import GitHubClient
from agents.schemas import ActionEnvelope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    action = ActionEnvelope.model_validate_json(Path(args.action).read_text(encoding="utf-8"))
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    default_branch = str(client.repository_info().get("default_branch") or "main")
    body = (
        f"{action.summary}\n\n"
        f"Rationale: {action.rationale}\n\n"
        f"Validated commit: `{args.sha}`\n\n"
        "<!-- zerofounder-ci-status -->\n"
        "CI state: `ci_not_started`\n\n"
        "This pull request is never merged automatically."
    )
    pull = client.create_pull_request(
        title=f"[ZeroFounder] {action.title}",
        body=body,
        head=args.branch,
        base=default_branch,
    )
    output = os.environ["GITHUB_OUTPUT"]
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"pr_number={int(pull['number'])}\n")
        handle.write(f"default_branch={default_branch}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
