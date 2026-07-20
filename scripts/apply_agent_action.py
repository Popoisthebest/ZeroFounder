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
    PreflightDecision,
    RepositoryCheckpoint,
)


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
    args = parser.parse_args()
    root = args.root.resolve()
    action = ActionEnvelope.model_validate_json(args.action.read_text())
    if action.action_type == ActionType.NO_OP:
        write_output("changed", "false")
        write_output("action_type", action.action_type.value)
        return 0
    executor = ActionExecutor(root)
    action = executor.prepare(action)
    args.action.write_text(action.model_dump_json(indent=2) + "\n")
    executor.apply_files(action)
    material = bool(action.files or action.state_transition)
    if action.state_transition:
        state_path = root / "company/state.json"
        state = CompanyState.model_validate_json(state_path.read_text())
        state.lifecycle_stage = action.state_transition.to_stage
        state.last_agent_run = datetime.now(UTC)
        state_path.write_text(state.model_dump_json(indent=2) + "\n")
    if material:
        checkpoint_path = root / "company/checkpoints.json"
        checkpoint = RepositoryCheckpoint.model_validate_json(checkpoint_path.read_text())
        decision = PreflightDecision.model_validate_json(args.preflight.read_text())
        updated = checkpoint_after_material_work(checkpoint, decision)
        updated.updated_at = datetime.now(UTC)
        checkpoint_path.write_text(updated.model_dump_json(indent=2) + "\n")
    write_output("changed", str(material).lower())
    write_output("action_type", action.action_type.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
