from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    default = str(client.repository_info().get("default_branch") or "main")
    pull = client.create_pull_request(
        title=f"[ZeroFounder] Approved dependency: {args.package}",
        body=(
            f"Approved proposal: `{args.proposal}`\n\nValidated commit: `{args.sha}`\n\n"
            "<!-- zerofounder-ci-status -->\nCI state: `ci_not_started`\n\n"
            "This dependency change is never merged automatically."
        ),
        head=args.branch,
        base=default,
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"pr_number={int(pull['number'])}\n")
        handle.write(f"default_branch={default}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
