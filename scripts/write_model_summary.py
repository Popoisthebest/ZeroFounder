from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_models import mask_secrets
from agents.schemas import ModelActionDiagnostic


def _safe_cell(value: object) -> str:
    return mask_secrets(str(value)).replace("|", "\\|").replace("\n", " ")[:500]


def render_summary(diagnostic: ModelActionDiagnostic) -> str:
    inference = diagnostic.inference
    original = diagnostic.original_action_type or "unavailable"
    rejection_code = diagnostic.rejection_code or "none"
    rejection_reason = diagnostic.rejection_reason or "none"
    allowed = ", ".join(item.value for item in diagnostic.allowed_action_types)
    validation_paths = ", ".join(inference.pydantic_validation_error_paths) or "none"
    rows = [
        ("lifecycle_stage", diagnostic.lifecycle_stage.value),
        ("allowed_action_types", allowed),
        ("original_model_action_type", original),
        ("validated_action_type", diagnostic.validated_action_type.value),
        ("accepted", str(diagnostic.accepted).lower()),
        ("selected_model", inference.selected_model or "unavailable"),
        ("request_mode", inference.request_mode or "unavailable"),
        ("http_status", inference.http_status or "unavailable"),
        (
            "choices_count",
            inference.choices_count
            if inference.choices_count is not None
            else "unavailable",
        ),
        ("message_content_type", inference.message_content_type or "unavailable"),
        ("response_char_count", inference.response_char_count),
        ("finish_reason", inference.finish_reason or "unavailable"),
        ("fallback_attempted", str(inference.fallback_attempted).lower()),
        ("retry_attempted", str(inference.retry_attempted).lower()),
        ("completed_inference_calls_this_run", inference.completed_inference_calls),
        ("reserved_inference_calls", inference.reserved_inference_calls),
        ("failed_after_request_calls_this_run", inference.failed_after_request_calls),
        ("failure_stage", inference.failure_stage or "none"),
        ("rejection_code", rejection_code),
        ("rejection_reason", rejection_reason),
        ("pydantic_validation_error_paths", validation_paths),
    ]
    table = "\n".join(f"| {_safe_cell(name)} | {_safe_cell(value)} |" for name, value in rows)
    return (
        "## ZeroFounder model action validation\n\n"
        "Only validated action metadata is shown; model text and credentials are omitted.\n\n"
        "| Field | Value |\n| --- | --- |\n"
        f"{table}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic", type=Path, required=True)
    args = parser.parse_args()
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return 0
    if args.diagnostic.exists():
        diagnostic = ModelActionDiagnostic.model_validate_json(args.diagnostic.read_text())
        summary = render_summary(diagnostic)
    else:
        summary = (
            "## ZeroFounder model action validation\n\n"
            "Diagnostic metadata was not produced; no model content or credentials were logged.\n"
        )
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
