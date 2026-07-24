from __future__ import annotations

import os
from pathlib import Path

from agents.github_models import mask_secrets


def _safe(value: object) -> str:
    return mask_secrets(str(value)).replace("|", "\\|").replace("\n", " ")[:500]


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").lower() == "true"


def render_summary() -> str:
    accepted = _env_bool("MODEL_ACCEPTED")
    model_result = os.getenv("MODEL_RESULT", "skipped")
    validated_action_type = os.getenv("VALIDATED_ACTION_TYPE", "none")
    downstream_skipped = not (
        _env_bool("BRANCH_CREATED")
        or _env_bool("PR_CREATED")
        or _env_bool("QUALITY_GATE_EXECUTED")
        or _env_bool("DEPENDENCY_APPROVAL_CREATED")
    )
    if model_result == "success" and not accepted:
        workflow_outcome = "action_rejected"
        downstream_skip_reason = "model_action_rejected"
    elif model_result == "skipped":
        workflow_outcome = "model_skipped"
        downstream_skip_reason = "model_not_run"
    elif accepted:
        workflow_outcome = "action_accepted"
        downstream_skip_reason = "none" if not downstream_skipped else "no_material_change"
    else:
        workflow_outcome = "workflow_incomplete"
        downstream_skip_reason = "upstream_job_not_successful"
    if validated_action_type == "no_op" and accepted:
        downstream_skip_reason = "no_material_change"
    rows = [
        ("workflow_outcome", workflow_outcome),
        ("model_job_result", model_result),
        ("model_action_type", os.getenv("MODEL_ACTION_TYPE", "none")),
        ("validated_action_type", validated_action_type),
        ("accepted", str(accepted).lower()),
        ("failure_stage", os.getenv("FAILURE_STAGE", "none")),
        ("rejection_code", os.getenv("REJECTION_CODE", "none")),
        ("correction_attempted", os.getenv("CORRECTION_ATTEMPTED", "false")),
        (
            "correction_response_was_request_echo",
            os.getenv("CORRECTION_RESPONSE_WAS_REQUEST_ECHO", "false"),
        ),
        ("downstream_jobs_skipped", str(downstream_skipped).lower()),
        ("downstream_skip_reason", downstream_skip_reason),
        ("branch_created", os.getenv("BRANCH_CREATED", "false")),
        ("pr_created", os.getenv("PR_CREATED", "false")),
        ("quality_gate_executed", os.getenv("QUALITY_GATE_EXECUTED", "false")),
        (
            "dependency_approval_required",
            os.getenv("DEPENDENCY_APPROVAL_REQUIRED", "false"),
        ),
        (
            "dependency_approval_created",
            os.getenv("DEPENDENCY_APPROVAL_CREATED", "false"),
        ),
    ]
    table = "\n".join(f"| {_safe(name)} | {_safe(value)} |" for name, value in rows)
    return (
        "## ZeroFounder 최종 실행 결과\n\n"
        "workflow 실행 성공 여부와 에이전트 action 수락 여부를 분리해 표시합니다.\n\n"
        "| 항목 | 값 |\n| --- | --- |\n"
        f"{table}\n"
    )


def main() -> int:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return 0
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(render_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
