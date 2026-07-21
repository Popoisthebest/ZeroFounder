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
    "disallowed_file": "허용되지 않은 파일 변경",
    "deleted_file": "파일 삭제 감지",
    "too_many_files": "변경 파일 수 제한 초과",
    "invalid_checkpoint_change": "checkpoint 변경 검증 실패",
    "invalid_state_change": "상태 변경 검증 실패",
    "invalid_problem_path": "문제 후보 경로 또는 내용 검증 실패",
    "quality_check_not_started": "품질검사 시작 안 됨",
}


def render_status_body(
    body: str,
    *,
    status: str,
    verified_sha: str = "",
    failed_check: str = "",
    run_url: str = "",
    rejection_code: str = "",
    rejection_reason: str = "",
    rejected_files: list[str] | None = None,
    allowed_files: list[str] | None = None,
    changed_files_count: int = 0,
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
        f"- 거부 코드: `{rejection_code or '없음'}`",
        f"- 거부 사유: {rejection_reason or '없음'}",
        f"- 거부 파일: {', '.join(rejected_files or []) or '없음'}",
        f"- 허용 파일: {', '.join(allowed_files or []) or '없음'}",
        f"- 변경 파일 수: {changed_files_count}",
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
    parser.add_argument("--rejection-code", default="")
    parser.add_argument("--rejection-reason", default="")
    parser.add_argument("--rejected-file", action="append", default=[])
    parser.add_argument("--allowed-file", action="append", default=[])
    parser.add_argument("--changed-files-count", type=int, default=0)
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
        rejection_code=args.rejection_code,
        rejection_reason=args.rejection_reason,
        rejected_files=args.rejected_file,
        allowed_files=args.allowed_file,
        changed_files_count=args.changed_files_count,
    )
    client.update_pull_request_body(args.pr, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
