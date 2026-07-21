from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from agents.action_executor import ActionExecutor
from agents.preflight import checkpoint_after_material_work
from agents.schemas import (
    ActionEnvelope,
    ActionType,
    CompanyState,
    MaterializedActionEnvelope,
    PreflightDecision,
    RepositoryCheckpoint,
)


def apply_validated_action(
    root: Path,
    action_path: Path,
    preflight_path: Path,
    *,
    materialized_output_path: Path | None = None,
    applied_at: datetime | None = None,
) -> tuple[MaterializedActionEnvelope, bool]:
    action = ActionEnvelope.model_validate_json(action_path.read_text())
    if action.action_type == ActionType.NO_OP:
        materialized = MaterializedActionEnvelope.from_model_action(action)
        if materialized_output_path:
            materialized_output_path.parent.mkdir(parents=True, exist_ok=True)
            materialized_output_path.write_text(
                materialized.model_dump_json(indent=2, by_alias=True) + "\n"
            )
        return materialized, False
    executor = ActionExecutor(root)
    materialized = executor.prepare(action)
    if materialized_output_path:
        materialized_output_path.parent.mkdir(parents=True, exist_ok=True)
        materialized_output_path.write_text(
            materialized.model_dump_json(indent=2, by_alias=True) + "\n"
        )
    executor.apply_files(materialized)
    material = bool(materialized.files or materialized.state_transition)
    if materialized.state_transition:
        state_path = root / "company/state.json"
        state = CompanyState.model_validate_json(state_path.read_text())
        state.lifecycle_stage = materialized.state_transition.to_stage
        if (
            materialized.action_type == ActionType.CREATE_PROBLEM_CANDIDATE
            and materialized.problem_candidate
        ):
            state.active_problem_id = materialized.problem_candidate.problem_id
        state.last_agent_run = applied_at or datetime.now(UTC)
        state_path.write_text(state.model_dump_json(indent=2) + "\n")
    if material:
        checkpoint_path = root / "company/checkpoints.json"
        checkpoint = RepositoryCheckpoint.model_validate_json(checkpoint_path.read_text())
        decision = PreflightDecision.model_validate_json(preflight_path.read_text())
        updated = checkpoint_after_material_work(checkpoint, decision)
        updated.updated_at = applied_at or datetime.now(UTC)
        checkpoint_path.write_text(updated.model_dump_json(indent=2) + "\n")
    return materialized, material


def write_output(name: str, value: str) -> None:
    import os

    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--materialized-output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    action, material = apply_validated_action(
        root,
        args.action,
        args.preflight,
        materialized_output_path=args.materialized_output,
    )
    write_output("changed", str(material).lower())
    write_output("action_type", action.action_type.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
