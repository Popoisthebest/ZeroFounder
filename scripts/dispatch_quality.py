from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agents.github_client import GitHubClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    status = "awaiting_ci_approval"
    try:
        GitHubClient(
            os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
        ).dispatch_quality_check(
            pr_number=args.pr,
            agent_branch=args.branch,
            commit_sha=args.sha,
            ref=args.ref,
        )
    except Exception as exc:
        status = "ci_not_started"
        detail = str(exc)[:300]
    else:
        detail = "quality-check workflow dispatched"
    args.result.write_text(json.dumps({"status": status, "detail": detail}) + "\n")
    print(status)
    return 0 if status == "awaiting_ci_approval" else 1


if __name__ == "__main__":
    raise SystemExit(main())
