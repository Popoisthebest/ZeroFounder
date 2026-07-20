from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_client import GitHubClient
from agents.quality import review_status
from scripts.update_pr_status import render_status_body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args()
    validation_status = os.getenv("VALIDATION_STATUS", "quality_check_not_started")
    status = review_status(validation_status)
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    pull = client.pull_request(args.pr)
    body = render_status_body(
        str(pull.get("body") or ""),
        status=status,
        verified_sha=os.getenv("VERIFIED_SHA", ""),
        failed_check=os.getenv("FAILED_CHECK", ""),
        run_url=os.getenv("QUALITY_RUN_URL", ""),
    )
    client.update_pull_request_body(args.pr, body)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"recorded_status={status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
