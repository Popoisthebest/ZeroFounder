from __future__ import annotations

import os

from agents.github_client import GitHubClient

LABELS = {
    "market-signal": ("1d76db", "공개 시장 근거"),
    "idea": ("5319e7", "사업 아이템"),
    "tool-request": ("fbca04", "의존성 또는 도구 승인"),
    "feature-request": ("84b6eb", "제품 기능 요청"),
    "bug": ("d73a4a", "확인되거나 신고된 버그"),
    "feedback": ("0e8a16", "사용자 피드백"),
    "agent-request": ("0052cc", "에이전트 실행 요청"),
    "founder-approval": ("b60205", "창업자 결정 필요"),
    "requires-approval": ("b60205", "사람의 승인 필요"),
    "agent-generated": ("bfdadc", "ZeroFounder 자동 생성"),
    "experiment": ("c5def5", "성장 또는 검증 실험"),
    "pivot-review": ("d4c5f9", "피벗 전제조건 및 검토"),
    "high-risk": ("8b0000", "자동 실행하지 않는 고위험 행동"),
}


def main() -> int:
    GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]).ensure_labels(LABELS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
