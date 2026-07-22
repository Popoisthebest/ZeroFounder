from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_models import mask_secrets
from agents.schemas import PreflightDecision


def _safe(value: object) -> str:
    return mask_secrets(str(value)).replace("|", "\\|").replace("\n", " ")[:500]


def render_summary(decision: PreflightDecision) -> str:
    lifecycle_stage = decision.lifecycle_stage.value if decision.lifecycle_stage else "unknown"
    expected_action_type = (
        decision.expected_action_type.value if decision.expected_action_type else "none"
    )
    open_pr_numbers = (
        ", ".join(f"#{number}" for number in decision.open_agent_pr_numbers) or "none"
    )
    rows = [
        ("lifecycle_stage", lifecycle_stage),
        ("active_problem_id", decision.active_problem_id or "none"),
        ("expected_action_type", expected_action_type),
        ("open_agent_pr_count", decision.open_agent_pr_count),
        ("open_agent_pr_numbers", open_pr_numbers),
        ("new_signal_count", len(decision.new_signal_ids)),
        ("new_issue_count", len(decision.issue_ids)),
        ("new_comment_count", len(decision.comment_ids)),
        ("idempotency_key_seen", str(decision.idempotency_key_seen).lower()),
        ("concurrent_run_detected", str(decision.concurrent_run_detected).lower()),
        ("should_call_model", str(decision.should_call_model).lower()),
        ("skipped", str(not decision.should_call_model).lower()),
        ("skip_reason", decision.skip_reason or "none"),
        ("skip_detail", decision.skip_detail or "none"),
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
    if decision.schedule_cron or decision.next_schedule_note:
        rows.extend(
            [
                ("schedule_cron", decision.schedule_cron or "none"),
                (
                    "next_schedule",
                    decision.next_schedule_note
                    or "다음 실행은 GitHub 스케줄에 따라 진행됩니다.",
                ),
            ]
        )
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
