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
        ("should_call_model", str(decision.should_call_model).lower()),
        ("completed calls today", decision.completed_calls_today),
        ("active reservations", decision.active_reservations),
        ("required calls for this run", decision.required_calls),
        ("daily limit", decision.daily_limit),
        ("manual diagnostic allowance", decision.manual_diagnostic_allowance),
        ("effective daily limit", decision.effective_daily_limit),
        ("allowed", str(decision.usage_allowed).lower()),
        ("limit calculation", decision.usage_calculation),
        ("failed after request calls today", decision.failed_after_request_calls_today),
        ("skipped runs today", decision.skipped_runs_today),
        ("blocked_reason", decision.blocked_reason or "none"),
    ]
    table = "\n".join(f"| {_safe(key)} | {_safe(value)} |" for key, value in rows)
    return (
        "## ZeroFounder model usage preflight\n\n"
        "Only confirmed inference markers count toward the daily limit.\n\n"
        "| Field | Value |\n| --- | --- |\n"
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
