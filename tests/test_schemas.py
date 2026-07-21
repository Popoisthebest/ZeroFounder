import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.bootstrap import initial_company_state
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
    DiscoveryActionEnvelope,
    LifecycleStage,
    MaterializedActionEnvelope,
    ModelActionDiagnostic,
    ModelInferenceDiagnostic,
)


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


def discovery_problem(**overrides):
    payload = {
        "role": "researcher",
        "action_type": "create_problem_candidate",
        "title": "Problem candidate",
        "summary": "Create an evidence-backed problem candidate.",
        "rationale": "Stored signals show the same manual workaround.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": ["signal-001"],
        "problem_candidate": {
            "problem_id": "problem-001",
            "title": "Repeated manual coordination",
            "target_users": ["small teams"],
            "description": "Small teams repeatedly reconcile coordination details manually.",
            "current_workaround": "They combine spreadsheets and message threads.",
        },
        "state_transition": {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
    }
    payload.update(overrides)
    return payload


def test_bootstrap_initial_state_is_discovery():
    state = initial_company_state()
    assert state.lifecycle_stage == LifecycleStage.DISCOVERY
    assert state.selected_venture is None


@pytest.mark.parametrize("stage", list(LifecycleStage))
def test_runtime_state_accepts_every_supported_lifecycle_stage(stage: LifecycleStage):
    state = CompanyState.model_validate(
        {**initial_company_state().model_dump(mode="json"), "lifecycle_stage": stage}
    )
    assert state.lifecycle_stage == stage


def test_extra_fields_and_unknown_actions_are_rejected():
    with pytest.raises(ValidationError):
        valid_action(untrusted_shell="rm -rf .")
    with pytest.raises(ValidationError):
        valid_action(action_type="run_shell")


def test_discovery_problem_candidate_contract_accepts_valid_payload():
    action = DiscoveryActionEnvelope.model_validate(discovery_problem()).to_action_envelope()
    assert action.problem_candidate is not None
    assert action.files == []


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ({"problem_candidate": None}, "problem_candidate"),
        ({"evidence_ids": []}, "evidence_ids"),
        ({"state_transition": {"from": "DISCOVERY", "to": "MVP_BUILDING"}}, "to"),
    ],
)
def test_discovery_problem_candidate_rejects_missing_or_invalid_fields(
    mutation: dict, expected_path: str
):
    with pytest.raises(ValidationError) as captured:
        DiscoveryActionEnvelope.model_validate(discovery_problem(**mutation))
    paths = [".".join(str(item) for item in error["loc"]) for error in captured.value.errors()]
    assert any(expected_path in path for path in paths)


def test_discovery_problem_candidate_rejects_extra_fields_and_wrong_types():
    extra = discovery_problem()
    extra["files"] = [{"path": "research/problems/invented.json", "content": "{}"}]
    with pytest.raises(ValidationError) as captured:
        DiscoveryActionEnvelope.model_validate(extra)
    assert any(error["type"] == "extra_forbidden" for error in captured.value.errors())

    wrong_type = discovery_problem()
    wrong_type["problem_candidate"]["title"] = 42
    with pytest.raises(ValidationError) as captured:
        DiscoveryActionEnvelope.model_validate(wrong_type)
    assert any(error["type"] == "string_type" for error in captured.value.errors())


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


def idea_candidate(idea_id: str = "idea-001", evidence_ids: list[str] | None = None):
    return {
        "idea_id": idea_id,
        "name": "List jump helper",
        "summary": "긴 목록의 반복 탐색을 줄이는 작은 조작 도구입니다.",
        "target_users": ["operators"],
        "proposed_solution": "목록에서 일정 간격 이동과 위치 복귀를 제공합니다.",
        "value_proposition": "반복 스크롤과 수동 위치 기억을 줄입니다.",
        "differentiation": "범용 검색이 아니라 반복 탐색 마찰만 직접 줄입니다.",
        "revenue_model": "팀 공유 설정을 유료 기능으로 둘 수 있습니다.",
        "feasibility": "정적 브라우저 MVP로 구현할 수 있습니다.",
        "evidence_ids": evidence_ids or ["signal-001"],
        "risks": ["사용자가 기존 방식에 머물 수 있습니다."],
        "evaluation_dimensions": ["무료 MVP 구현성", "반복 사용 가능성"],
    }


def test_create_idea_candidates_action_accepts_valid_candidates():
    action = valid_action(
        action_type="create_idea_candidates",
        files=[],
        evidence_ids=[],
        idea_candidates=[idea_candidate("idea-001"), idea_candidate("idea-002")],
    )
    assert action.idea_candidates is not None
    assert [item.idea_id for item in action.idea_candidates] == ["idea-001", "idea-002"]
    candidate = idea_candidate("idea-003")
    candidate["revenue_model"] = "Recurring team subscription revenue can fund maintenance."
    assert valid_action(
        action_type="create_idea_candidates",
        files=[],
        idea_candidates=[candidate, idea_candidate("idea-004")],
    )


def test_idea_candidates_are_rejected_on_other_actions_or_bad_shape():
    with pytest.raises(ValidationError):
        valid_action(
            action_type="write_report",
            files=[],
            idea_candidates=[idea_candidate("idea-001"), idea_candidate("idea-002")],
        )
    with pytest.raises(ValidationError):
        valid_action(action_type="write_report", files=[], idea_candidates=[])
    with pytest.raises(ValidationError):
        valid_action(action_type="no_op", files=[], idea_candidates=[])
    with pytest.raises(ValidationError):
        valid_action(
            action_type="create_idea_candidates",
            files=[],
            idea_candidates=[idea_candidate("idea-001"), idea_candidate("idea-001")],
        )
    with pytest.raises(ValidationError):
        valid_action(
            action_type="create_idea_candidates",
            files=[],
            idea_candidates=[idea_candidate(f"idea-{index:03}") for index in range(9)],
        )
    invented_url = idea_candidate("idea-001")
    invented_url["summary"] = "https://example.test 에서 가져온 검증되지 않은 아이디어입니다."
    with pytest.raises(ValidationError):
        valid_action(
            action_type="create_idea_candidates",
            files=[],
            idea_candidates=[invented_url, idea_candidate("idea-002")],
        )
    invented_metric = idea_candidate("idea-001")
    invented_metric["value_proposition"] = "사용자 1000명을 확보할 수 있다는 수치 주장입니다."
    with pytest.raises(ValidationError):
        valid_action(
            action_type="create_idea_candidates",
            files=[],
            idea_candidates=[invented_metric, idea_candidate("idea-002")],
        )


def test_create_idea_candidates_raw_and_materialized_schema_are_separate():
    raw_payload = {
        "role": "researcher",
        "action_type": "create_idea_candidates",
        "title": "Create idea candidates",
        "summary": "Generate evidence-backed ideas.",
        "rationale": "Validated evidence is available.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": ["signal-001"],
        "idea_candidates": [idea_candidate("idea-001"), idea_candidate("idea-002")],
    }
    assert ActionEnvelope.model_validate(raw_payload)
    with pytest.raises(ValidationError):
        ActionEnvelope.model_validate(
            {
                **raw_payload,
                "files": [
                    {
                        "path": "research/ideas/problem-001.json",
                        "content": "{}\n",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        ActionEnvelope.model_validate(
            {
                **raw_payload,
                "state_transition": {"from": "IDEA_EVALUATION", "to": "IDEA_SELECTED"},
            }
        )

    materialized_payload = {
        key: value
        for key, value in raw_payload.items()
        if key not in {"idea_candidates"}
    } | {
        "source": "trusted_materializer",
        "files": [
            {
                "path": "research/ideas/problem-001.json",
                "content": '{"problem_id":"problem-001","idea_candidates":[]}\n',
            }
        ],
    }
    assert MaterializedActionEnvelope.model_validate(materialized_payload)
    with pytest.raises(ValidationError):
        MaterializedActionEnvelope.model_validate(
            materialized_payload
            | {
                "files": [
                    {
                        "path": "research/ideas/problem-other.json",
                        "content": "{}\n",
                    },
                    {"path": "reports/extra.md", "content": "extra\n"},
                ]
            }
        )
    with pytest.raises(ValidationError):
        MaterializedActionEnvelope.model_validate(
            materialized_payload
            | {
                "state_transition": {
                    "from": "IDEA_EVALUATION",
                    "to": "IDEA_SELECTED",
                }
            }
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
    with pytest.raises(ValidationError):
        ModelInferenceDiagnostic.model_validate({"failure_stage": "unknown_stage"})
