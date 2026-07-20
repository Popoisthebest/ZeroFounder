from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubAPIError, GitHubClient
from agents.quality import candidate_change_paths_allowed, classify_pull_target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    try:
        pull = client.pull_request(args.pr)
    except (GitHubAPIError, ValueError):
        status, verified_sha = "invalid_pr", ""
    else:
        status, verified_sha = classify_pull_target(
            pull,
            repository=client.repository,
            branch=args.branch,
            commit_sha=args.sha,
        )
        if status == "valid":
            try:
                files = client.pull_request_files(args.pr)
            except (GitHubAPIError, ValueError):
                status = "invalid_pr"
            else:
                if not candidate_change_paths_allowed(args.branch, files):
                    status = "invalid_pr"
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"validation_status={status}\n")
            handle.write(f"sha={verified_sha}\n")
    print(f"PR 검증 결과: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
