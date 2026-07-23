from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_client import GitHubClient
from agents.quality import review_status
from scripts.update_pr_status import render_status_body


def _rejected_files() -> list[str]:
    import json

    try:
        value = json.loads(os.getenv("REJECTED_FILES", "[]"))
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _allowed_files() -> list[str]:
    import json

    try:
        value = json.loads(os.getenv("ALLOWED_FILES", "[]"))
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


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
        rejection_code=os.getenv("REJECTION_CODE", ""),
        rejection_reason=os.getenv("REJECTION_REASON", ""),
        rejected_files=_rejected_files(),
        allowed_files=_allowed_files(),
        changed_files_count=int(os.getenv("CHANGED_FILES_COUNT", "0") or 0),
        report_type=os.getenv("REPORT_TYPE", ""),
        report_period=os.getenv("REPORT_PERIOD", ""),
        artifact_path=os.getenv("ARTIFACT_PATH", ""),
        operation_key=os.getenv("OPERATION_KEY", ""),
    )
    client.update_pull_request_body(args.pr, body)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"recorded_status={status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
