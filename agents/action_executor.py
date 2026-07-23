from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agents.idea_materializer import materialize_idea_candidates, materialize_idea_evaluation
from agents.lifecycle import validate_transition
from agents.problem_materializer import materialize_problem_candidate
from agents.report_materializer import materialize_report
from agents.safety import (
    validate_action_files,
    validate_evidence_references,
    validate_model_urls,
)
from agents.schemas import ActionEnvelope, ActionType, CompanyState, MaterializedActionEnvelope


class ActionExecutionError(RuntimeError):
    pass


class ActionExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def validate(self, action: MaterializedActionEnvelope) -> None:
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

    def prepare(self, action: ActionEnvelope) -> MaterializedActionEnvelope:
        files = action.files
        if action.action_type == ActionType.CREATE_PROBLEM_CANDIDATE:
            files = [materialize_problem_candidate(action, self.root)]
        if action.action_type == ActionType.CREATE_IDEA_CANDIDATES:
            files = [materialize_idea_candidates(action, self.root)]
        if action.action_type == ActionType.EVALUATE_IDEAS and not files:
            files = [materialize_idea_evaluation(action, self.root)]
        if action.action_type == ActionType.WRITE_REPORT:
            files = [materialize_report(action, self.root)]
        return MaterializedActionEnvelope.from_model_action(action, files=files)

    def apply_files(self, action: MaterializedActionEnvelope) -> list[Path]:
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
