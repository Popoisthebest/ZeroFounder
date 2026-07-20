import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.safety import (
    SafetyViolation,
    validate_action_files,
    validate_evidence_references,
    validate_model_urls,
)
from agents.schemas import (
    ActionEnvelope,
    ActionType,
    CompanyState,
    LifecycleStage,
    ModelActionDiagnostic,
)

ROOT = Path(__file__).parents[1]


def valid_action(**overrides):
    payload = {
        "role": "builder",
        "action_type": "create_code_patch",
        "title": "Safe patch",
        "summary": "Add a tested product component",
        "rationale": "Approved MVP task",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": [],
        "files": [{"path": "venture/product/src/App.tsx", "content": "export default 1"}],
    }
    payload.update(overrides)
    return ActionEnvelope.model_validate(payload)


def test_initial_state_is_discovery():
    state = CompanyState.model_validate_json((ROOT / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.DISCOVERY
    assert state.selected_venture is None


def test_extra_fields_and_unknown_actions_are_rejected():
    with pytest.raises(ValidationError):
        valid_action(untrusted_shell="rm -rf .")
    with pytest.raises(ValidationError):
        valid_action(action_type="run_shell")


@pytest.mark.parametrize(
    "path",
    [
        "../agents/x.py",
        "/tmp/x",
        ".github/workflows/pwn.yml",
        "package.json",
        "founder/results.json",
    ],
)
def test_protected_paths_are_rejected(path: str, tmp_path: Path):
    action = valid_action(files=[{"path": path, "content": "x"}])
    with pytest.raises(SafetyViolation):
        validate_action_files(action, workspace=tmp_path)


def test_size_limits_are_enforced(tmp_path: Path):
    action = valid_action(files=[{"path": "venture/product/a.ts", "content": "12345"}])
    with pytest.raises(SafetyViolation):
        validate_action_files(action, workspace=tmp_path, max_file_chars=4)
    with pytest.raises(SafetyViolation):
        validate_action_files(action, workspace=tmp_path, max_total_chars=4)


def test_unknown_evidence_rejects_entire_action(tmp_path: Path):
    action = valid_action(evidence_ids=["missing-001"])
    with pytest.raises(SafetyViolation):
        validate_evidence_references(action, tmp_path)


def test_existing_evidence_is_accepted(tmp_path: Path):
    target = tmp_path / "signals/processed"
    target.mkdir(parents=True)
    (target / "e.json").write_text(
        json.dumps({"evidence_id": "evidence-001", "url": "https://example.com"})
    )
    action = valid_action(evidence_ids=["evidence-001"])
    assert "evidence-001" in validate_evidence_references(action, tmp_path)


def test_model_cannot_invent_evidence_url(tmp_path: Path):
    target = tmp_path / "signals/processed"
    target.mkdir(parents=True)
    (target / "e.json").write_text(
        json.dumps({"evidence_id": "evidence-001", "url": "https://trusted.example/a"})
    )
    action = valid_action(
        action_type="select_idea",
        evidence_ids=["evidence-001"],
        files=[
            {
                "path": "ideas/selected/decision.md",
                "content": "Invented https://attacker.example/fake",
            }
        ],
    )
    evidence = validate_evidence_references(action, tmp_path)
    with pytest.raises(SafetyViolation):
        validate_model_urls(action, evidence)


def test_no_op_cannot_modify_state():
    with pytest.raises(ValidationError):
        valid_action(
            role="auditor",
            action_type=ActionType.NO_OP,
            files=[],
            state_transition={"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
        )


@pytest.mark.parametrize("action_type", ["create_problem_candidate", "validate_evidence"])
def test_discovery_analysis_requires_evidence_and_material_output(action_type: str):
    with pytest.raises(ValidationError):
        valid_action(action_type=action_type, files=[], evidence_ids=[])


def test_model_diagnostic_rejects_unknown_fields_and_inconsistent_acceptance():
    payload = {
        "lifecycle_stage": "DISCOVERY",
        "allowed_action_types": ["no_op"],
        "original_action_type": "no_op",
        "validated_action_type": "no_op",
        "accepted": True,
    }
    assert ModelActionDiagnostic.model_validate(payload).accepted
    with pytest.raises(ValidationError):
        ModelActionDiagnostic.model_validate({**payload, "raw_model_text": "secret"})
    with pytest.raises(ValidationError):
        ModelActionDiagnostic.model_validate(
            {
                **payload,
                "accepted": False,
                "rejection_code": None,
                "rejection_reason": None,
            }
        )
