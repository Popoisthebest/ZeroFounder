from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubClient

MARKER = "<!-- zerofounder-ci-status -->"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument(
        "--status",
        choices=["ci_not_started", "awaiting_ci_approval", "ci_failed", "ready_for_human_review"],
        required=True,
    )
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    pull = client.pull_request(args.pr)
    body = str(pull.get("body") or "")
    lines = body.splitlines()
    try:
        index = lines.index(MARKER)
        lines[index + 1] = f"CI state: `{args.status}`"
    except (ValueError, IndexError):
        lines.extend(["", MARKER, f"CI state: `{args.status}`"])
    client.update_pull_request_body(args.pr, "\n".join(lines).strip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
