from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_models import mask_secrets
from agents.schemas import PreflightDecision


def _safe(value: object) -> str:
    return mask_secrets(str(value)).replace("|", "\\|").replace("\n", " ")[:500]


def render_summary(decision: PreflightDecision) -> str:
    rows = [
        ("skipped", str(not decision.should_call_model).lower()),
        ("skip_reason", decision.blocked_reason or "none"),
        ("should_call_model", str(decision.should_call_model).lower()),
        ("오늘 완료된 호출", decision.completed_calls_today),
        ("활성 예약", decision.active_reservations),
        ("이번 실행 필요 호출", decision.required_calls),
        ("일일 한도", decision.daily_limit),
        ("수동 진단 추가 한도", decision.manual_diagnostic_allowance),
        ("적용 일일 한도", decision.effective_daily_limit),
        ("호출 허용", str(decision.usage_allowed).lower()),
        ("한도 계산식", decision.usage_calculation),
        ("오늘 요청 후 실패", decision.failed_after_request_calls_today),
        ("오늘 건너뛴 실행", decision.skipped_runs_today),
        ("blocked_reason", decision.blocked_reason or "none"),
    ]
    table = "\n".join(f"| {_safe(key)} | {_safe(value)} |" for key, value in rows)
    return (
        "## ZeroFounder 모델 사용량 사전 점검\n\n"
        "실제 inference 요청이 확인된 호출만 일일 한도에 포함합니다.\n\n"
        "| 항목 | 값 |\n| --- | --- |\n"
        f"{table}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    args = parser.parse_args()
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path or not args.preflight.exists():
        return 0
    decision = PreflightDecision.model_validate_json(args.preflight.read_text())
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(render_summary(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
