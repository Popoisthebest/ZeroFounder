from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agents.lifecycle import validate_transition
from agents.safety import (
    validate_action_files,
    validate_evidence_references,
    validate_model_urls,
)
from agents.schemas import ActionEnvelope, CompanyState


class ActionExecutionError(RuntimeError):
    pass


class ActionExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def validate(self, action: ActionEnvelope) -> None:
        validate_action_files(action, workspace=self.root)
        evidence = validate_evidence_references(action, self.root)
        validate_model_urls(action, evidence)
        if action.state_transition:
            state = CompanyState.model_validate_json((self.root / "company/state.json").read_text())
            if state.lifecycle_stage != action.state_transition.from_stage:
                raise ActionExecutionError(
                    "state transition source does not match repository state"
                )
            validate_transition(
                action.state_transition.from_stage, action.state_transition.to_stage
            )

    def apply_files(self, action: ActionEnvelope) -> list[Path]:
        self.validate(action)
        changed: list[Path] = []
        backups: dict[Path, bytes | None] = {}
        try:
            for change in action.files:
                target = self.root / change.path
                backups[target] = target.read_bytes() if target.exists() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.zerofounder-{os.getpid()}")
                temporary.write_text(change.content, encoding="utf-8")
                temporary.replace(target)
                changed.append(target)
        except OSError as exc:
            self._restore(backups)
            raise ActionExecutionError(str(exc)) from exc
        return changed

    @staticmethod
    def _restore(backups: dict[Path, bytes | None]) -> None:
        for target, content in backups.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)

    def append_decision(self, record_json: str) -> None:
        record = json.loads(record_json)
        destination = self.root / "company/decisions.jsonl"
        original = destination.read_bytes() if destination.exists() else b""
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        destination.write_bytes(original + line)
        if not destination.read_bytes().startswith(original):
            destination.write_bytes(original)
            raise ActionExecutionError("append-only decision integrity failed")


def run_fixed_quality_checks(root: Path) -> None:
    commands = [
        [str(root / ".venv/bin/python"), "-m", "pytest"],
        [str(root / ".venv/bin/ruff"), "check", "agents", "tests", "scripts"],
    ]
    if (root / "package.json").exists():
        commands.extend(
            [
                ["npm", "run", "lint"],
                ["npm", "run", "typecheck"],
                ["npm", "test", "--", "--run"],
                ["npm", "run", "build"],
            ]
        )
    for command in commands:
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            raise ActionExecutionError(f"quality check failed: {command[0]} {command[1]}")
