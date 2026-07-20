from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    if not client.verify_pull_head(pr_number=args.pr, branch=args.branch, commit_sha=args.sha):
        raise SystemExit("PR head SHA or branch does not match dispatched input")
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"sha={args.sha}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
