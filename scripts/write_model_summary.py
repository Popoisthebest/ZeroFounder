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
    validation_error_parts = []
    for item in inference.pydantic_validation_errors:
        candidate = (
            f" candidate={item.candidate_index}"
            if item.candidate_index is not None
            else ""
        )
        idea_id = f" idea_id={item.idea_id}" if item.idea_id else ""
        validator = f" validator={item.validator_name}" if item.validator_name else ""
        field = f" field={item.failure_field_path}" if item.failure_field_path else ""
        validation_error_parts.append(
            f"{item.path}: {item.error_type}{candidate}{idea_id}"
            f"{validator}{field} reason={item.message}"
        )
    validation_errors = "; ".join(validation_error_parts) or "none"
    missing_fields = ", ".join(
        item.missing_field
        for item in inference.pydantic_validation_errors
        if item.missing_field
    ) or "none"
    extra_fields = ", ".join(
        item.extra_field
        for item in inference.pydantic_validation_errors
        if item.extra_field
    ) or "none"
    expected_types = "; ".join(
        f"{item.path}: {item.expected_type}"
        for item in inference.pydantic_validation_errors
        if item.expected_type
    ) or "none"
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
        ("http_failed_calls_this_run", inference.http_failed_calls),
        (
            "response_validation_failed_calls_this_run",
            inference.response_validation_failed_calls,
        ),
        ("request_body_bytes", inference.request_body_bytes),
        ("system_prompt_chars", inference.system_prompt_chars),
        ("user_prompt_chars", inference.user_prompt_chars),
        ("schema_chars", inference.schema_chars),
        ("context_chars", inference.context_chars),
        ("estimated_input_tokens", inference.estimated_input_tokens),
        ("initial_estimated_tokens", inference.initial_estimated_tokens),
        ("correction_estimated_tokens", inference.correction_estimated_tokens),
        ("reserved_correction_tokens", inference.reserved_correction_tokens),
        ("compacted_context", str(inference.compacted_context).lower()),
        ("removed_context_sections", ", ".join(inference.removed_context_sections) or "none"),
        (
            "selected_model_max_input_tokens",
            inference.selected_model_max_input_tokens,
        ),
        ("applied_input_budget", inference.applied_input_budget),
        ("active_problem_id", inference.active_problem_id or "none"),
        ("candidate_evidence_id_count", inference.candidate_evidence_id_count),
        ("problem_loaded", str(inference.problem_loaded).lower()),
        ("problem_evidence_count", inference.problem_evidence_count),
        ("resolved_evidence_count", inference.resolved_evidence_count),
        (
            "existing_idea_candidate_count",
            inference.existing_idea_candidate_count,
        ),
        ("idea_context_ready", str(inference.idea_context_ready).lower()),
        ("generated_idea_candidate_count", inference.generated_idea_candidate_count),
        ("accepted_idea_candidate_count", inference.accepted_idea_candidate_count),
        ("rejected_idea_candidate_count", inference.rejected_idea_candidate_count),
        ("idea_candidate_ids", ", ".join(inference.idea_candidate_ids) or "none"),
        ("unresolved_evidence_ids", ", ".join(inference.unresolved_evidence_ids) or "none"),
        ("new_signal_count", inference.new_signal_count),
        ("included_signal_count", inference.included_signal_count),
        ("excluded_signal_count", inference.excluded_signal_count),
        ("compact_retry_attempted", str(inference.compact_retry_attempted).lower()),
        (
            "validation_correction_attempted",
            str(inference.validation_correction_attempted).lower(),
        ),
        ("failure_stage", inference.failure_stage or "none"),
        ("rejection_code", rejection_code),
        ("rejection_reason", rejection_reason),
        ("pydantic_validation_error_paths", validation_paths),
        ("pydantic_validation_errors", validation_errors),
        ("pydantic_validation_error_count", inference.pydantic_validation_error_count),
        ("missing_fields", missing_fields),
        ("extra_fields", extra_fields),
        ("expected_types", expected_types),
    ]
    table = "\n".join(f"| {_safe_cell(name)} | {_safe_cell(value)} |" for name, value in rows)
    return (
        "## ZeroFounder 모델 행동 검증\n\n"
        "검증된 행동 메타데이터만 표시하며 모델 원문과 인증 정보는 제외합니다.\n\n"
        "| 항목 | 값 |\n| --- | --- |\n"
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
            "## ZeroFounder 모델 행동 검증\n\n"
            "진단 메타데이터가 생성되지 않았으며 모델 원문과 인증 정보는 기록하지 않았습니다.\n"
        )
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
