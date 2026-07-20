from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.github_models import mask_secrets
from agents.schemas import ModelActionDiagnostic


def _safe_cell(value: object) -> str:
    return mask_secrets(str(value)).replace("|", "\\|").replace("\n", " ")[:500]


def render_summary(diagnostic: ModelActionDiagnostic) -> str:
    original = diagnostic.original_action_type or "unavailable"
    rejection_code = diagnostic.rejection_code or "none"
    rejection_reason = diagnostic.rejection_reason or "none"
    allowed = ", ".join(item.value for item in diagnostic.allowed_action_types)
    rows = [
        ("Lifecycle stage", diagnostic.lifecycle_stage.value),
        ("Allowed action types", allowed),
        ("Original model action_type", original),
        ("Validated action_type", diagnostic.validated_action_type.value),
        ("Accepted", str(diagnostic.accepted).lower()),
        ("Rejection code", rejection_code),
        ("Rejection reason", rejection_reason),
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
