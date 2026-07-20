from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubClient

MARKER = "<!-- zerofounder-ci-status -->"
STATUS_LABELS = {
    "ready_for_human_review": "사람의 최종 검토 준비 완료",
    "quality_check_failed": "품질검사 실패",
    "invalid_pr": "유효하지 않은 Pull Request",
    "branch_mismatch": "PR head branch 불일치",
    "sha_mismatch": "PR head SHA 불일치",
    "repository_mismatch": "PR 저장소 불일치",
    "quality_check_not_started": "품질검사 시작 안 됨",
}


def render_status_body(
    body: str,
    *,
    status: str,
    verified_sha: str = "",
    failed_check: str = "",
    run_url: str = "",
) -> str:
    if status not in STATUS_LABELS:
        raise ValueError("invalid quality review status")
    prefix = body.split(MARKER, 1)[0].rstrip()
    block = [
        MARKER,
        "## 품질검사 상태",
        "",
        f"- 상태: **{STATUS_LABELS[status]}** (`{status}`)",
        f"- 검증 SHA: `{verified_sha or '없음'}`",
        f"- 실패 검사: `{failed_check or '없음'}`",
        f"- 검사 실행: {run_url or '확인 불가'}",
        "",
        "자동 병합은 수행되지 않으며 창업자의 최종 검토가 필요합니다.",
    ]
    return f"{prefix}\n\n" + "\n".join(block) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument(
        "--status",
        choices=sorted(STATUS_LABELS),
        required=True,
    )
    parser.add_argument("--verified-sha", default="")
    parser.add_argument("--failed-check", default="")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    pull = client.pull_request(args.pr)
    body = str(pull.get("body") or "")
    updated = render_status_body(
        body,
        status=args.status,
        verified_sha=args.verified_sha,
        failed_check=args.failed_check,
        run_url=args.run_url,
    )
    client.update_pull_request_body(args.pr, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
