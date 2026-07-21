from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

import agents.orchestrator as orchestrator
from agents.action_executor import ActionExecutor
from agents.candidate_validator import validate_create_idea_candidates_content
from agents.quality import validate_changed_file_contract
from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    ActionType,
    AgentRole,
    CompanyState,
    LifecycleStage,
    MaterializedActionEnvelope,
    ModelCallResult,
    ModelInferenceDiagnostic,
    ModelRequestMode,
    ModelSelection,
    PreflightDecision,
    RiskLevel,
    StateTransition,
    TriggerReason,
)
from scripts.commit_agent_changes import (
    commit_agent_changes,
    validate_materialized_action_for_commit,
)
from tests.e2e.conftest import (
    APPLIED_AT,
    PROBLEM_ID,
    SIGNAL_IDS,
    E2EHarness,
    active_problem_payload,
    idea_candidate,
)


def _base_payload(action_type: ActionType) -> dict[str, object]:
    return {
        "role": AgentRole.RESEARCHER.value,
        "action_type": action_type.value,
        "title": "Lifecycle action",
        "summary": "Use the current repository context safely.",
        "rationale": "The required prior artifact exists in the repository.",
        "risk_level": RiskLevel.LOW.value,
        "requires_approval": False,
        "evidence_ids": [],
    }


def _idea_payload(**overrides: object) -> dict[str, object]:
    payload = _base_payload(ActionType.CREATE_IDEA_CANDIDATES)
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "idea_candidates": [
                idea_candidate("idea-001", ["signal-001"]),
                idea_candidate("idea-002", list(SIGNAL_IDS)),
            ],
        }
    )
    payload.update(overrides)
    return payload


def _prepare_active_problem(harness: E2EHarness, *, stage: LifecycleStage) -> None:
    harness.write_state(
        CompanyState(lifecycle_stage=stage, active_problem_id=PROBLEM_ID)
    )
    (harness.repo / "research/problems").mkdir(parents=True, exist_ok=True)
    (harness.repo / f"research/problems/{PROBLEM_ID}.json").write_text(
        json.dumps(active_problem_payload(), indent=2) + "\n",
        encoding="utf-8",
    )


def _manual_decision() -> PreflightDecision:
    return PreflightDecision(
        should_call_model=True,
        reasons=[TriggerReason.MANUAL],
        new_signal_ids=[],
        idempotency_key="f" * 64,
    )


def _install_single_action_model(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    class FakeGitHubModelsClient:
        def __init__(self, token: str, limiter: object) -> None:
            self.token = token
            self.limiter = limiter

        def catalog(self) -> list[dict[str, object]]:
            return [{"id": "fake/model"}]

        def select_chat_model(
            self,
            catalog: list[dict[str, object]],
            *,
            required_input_tokens: int,
        ) -> ModelSelection:
            assert catalog
            return ModelSelection(
                selected_model="fake/model",
                request_mode=ModelRequestMode.JSON_ONLY,
                max_input_tokens=16000,
                applied_input_budget=6000,
            )

        def chat_action(self, **kwargs: Any) -> ModelCallResult:
            action = ActionEnvelope.model_validate(payload)
            diagnostic = ModelInferenceDiagnostic(
                active_problem_id=kwargs["active_problem_id"],
                candidate_evidence_id_count=kwargs["candidate_evidence_id_count"],
                resolved_evidence_count=kwargs["resolved_evidence_count"],
                unresolved_evidence_ids=kwargs["unresolved_evidence_ids"],
                new_signal_count=kwargs["new_signal_count"],
                problem_loaded=kwargs["problem_loaded"],
                problem_evidence_count=kwargs["problem_evidence_count"],
                existing_idea_candidate_count=kwargs["existing_idea_candidate_count"],
                idea_context_ready=kwargs["idea_context_ready"],
                included_signal_count=kwargs["included_signal_count"],
            )
            return ModelCallResult(action=action, diagnostic=diagnostic)

    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeGitHubModelsClient)


def test_evidence_validation_uses_existing_problem_evidence_without_new_signals(
    e2e_harness: E2EHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_active_problem(e2e_harness, stage=LifecycleStage.EVIDENCE_VALIDATION)
    payload = _base_payload(ActionType.VALIDATE_EVIDENCE)
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "state_transition": {
                "from": LifecycleStage.EVIDENCE_VALIDATION.value,
                "to": LifecycleStage.IDEA_EVALUATION.value,
            },
        }
    )
    _install_single_action_model(monkeypatch, payload)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    outcome = orchestrator.run_model(e2e_harness.repo, _manual_decision())

    assert outcome.diagnostic.accepted
    assert outcome.diagnostic.inference.new_signal_count == 0
    assert outcome.diagnostic.inference.candidate_evidence_id_count == 2
    assert outcome.diagnostic.inference.resolved_evidence_count == 2


def test_missing_evidence_record_is_rejected_after_model_output(
    e2e_harness: E2EHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_active_problem(e2e_harness, stage=LifecycleStage.EVIDENCE_VALIDATION)
    records = [json.dumps({"signal_id": "signal-001", "url": "https://evidence.example/1"})]
    (e2e_harness.repo / "signals/raw/signals.jsonl").write_text(
        "\n".join(records) + "\n",
        encoding="utf-8",
    )
    payload = _base_payload(ActionType.VALIDATE_EVIDENCE)
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "state_transition": {
                "from": LifecycleStage.EVIDENCE_VALIDATION.value,
                "to": LifecycleStage.IDEA_EVALUATION.value,
            },
        }
    )
    _install_single_action_model(monkeypatch, payload)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    outcome = orchestrator.run_model(e2e_harness.repo, _manual_decision())

    assert not outcome.diagnostic.accepted
    assert outcome.diagnostic.rejection_code == ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED
    assert "missing_evidence_record" in (outcome.diagnostic.rejection_reason or "")
    assert outcome.diagnostic.inference.unresolved_evidence_ids == ["signal-002"]


def test_idea_context_reports_missing_active_problem_and_problem_record(
    e2e_harness: E2EHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_single_action_model(monkeypatch, _base_payload(ActionType.NO_OP))
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    e2e_harness.write_state(CompanyState(lifecycle_stage=LifecycleStage.IDEA_EVALUATION))
    missing_active = orchestrator.run_model(e2e_harness.repo, _manual_decision())
    assert missing_active.diagnostic.rejection_code == ActionRejectionCode.MISSING_ACTIVE_PROBLEM
    assert "missing_active_problem" in (missing_active.diagnostic.rejection_reason or "")

    e2e_harness.write_state(
        CompanyState(
            lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
            active_problem_id=PROBLEM_ID,
        )
    )
    missing_record = orchestrator.run_model(e2e_harness.repo, _manual_decision())
    assert missing_record.diagnostic.rejection_code == ActionRejectionCode.MISSING_PROBLEM_RECORD
    assert "missing_problem_record" in (missing_record.diagnostic.rejection_reason or "")


def test_idea_model_output_acceptance_and_rejection_paths(
    e2e_harness: E2EHarness,
) -> None:
    _prepare_active_problem(e2e_harness, stage=LifecycleStage.IDEA_EVALUATION)
    state = e2e_harness.read_state()
    inference = ModelInferenceDiagnostic(
        active_problem_id=PROBLEM_ID,
        problem_loaded=True,
        problem_evidence_count=2,
        resolved_evidence_count=2,
        idea_context_ready=True,
    )

    accepted = orchestrator.validate_model_action(
        e2e_harness.repo,
        state,
        ActionEnvelope.model_validate(_idea_payload()),
        inference,
    )
    assert accepted.diagnostic.accepted
    assert accepted.diagnostic.inference.accepted_idea_candidate_count == 2

    duplicate = _idea_payload()
    duplicate["idea_candidates"][1]["idea_id"] = "idea-001"  # type: ignore[index]
    with pytest.raises(ValidationError, match="idea_id values must be unique"):
        ActionEnvelope.model_validate(duplicate)

    invalid_evidence = _idea_payload()
    invalid_evidence["idea_candidates"][0]["evidence_ids"] = ["signal-other"]  # type: ignore[index]
    rejected = orchestrator.validate_model_action(
        e2e_harness.repo,
        state,
        ActionEnvelope.model_validate(invalid_evidence),
        inference,
    )
    assert rejected.diagnostic.rejection_code == ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED

    wrong_action = _base_payload(ActionType.VALIDATE_EVIDENCE)
    wrong_action["idea_candidates"] = [idea_candidate("idea-001", ["signal-001"])]
    with pytest.raises(ValidationError, match="idea_candidates is only valid"):
        ActionEnvelope.model_validate(wrong_action)

    too_many = _idea_payload()
    too_many["idea_candidates"] = [
        idea_candidate(f"idea-{index:03d}", ["signal-001"]) for index in range(9)
    ]
    with pytest.raises(ValidationError):
        ActionEnvelope.model_validate(too_many)


def test_raw_and_materialized_idea_actions_are_strictly_separated(
    e2e_harness: E2EHarness,
) -> None:
    _prepare_active_problem(e2e_harness, stage=LifecycleStage.IDEA_EVALUATION)
    raw_with_files = _idea_payload(
        files=[
            {
                "path": f"research/ideas/{PROBLEM_ID}.json",
                "content": "{}\n",
                "operation": "upsert",
            }
        ]
    )
    with pytest.raises(ValidationError, match="cannot provide files"):
        ActionEnvelope.model_validate(raw_with_files)

    raw_with_transition = _idea_payload(
        state_transition={
            "from": LifecycleStage.IDEA_EVALUATION.value,
            "to": LifecycleStage.DISTRIBUTION_CHECK.value,
        }
    )
    with pytest.raises(ValidationError, match="cannot provide files or state_transition"):
        ActionEnvelope.model_validate(raw_with_transition)

    raw = ActionEnvelope.model_validate(_idea_payload())
    materialized = ActionExecutor(e2e_harness.repo).prepare(raw)
    assert materialized.files[0].path == f"research/ideas/{PROBLEM_ID}.json"
    validate_materialized_action_for_commit(materialized, e2e_harness.repo)

    wrong_path = MaterializedActionEnvelope.from_model_action(
        raw,
        files=[
            {
                "path": "research/ideas/problem-other.json",
                "content": materialized.files[0].content,
                "operation": "upsert",
            }
        ],
    )
    with pytest.raises(ValueError, match="materialized file path is not allowed"):
        validate_materialized_action_for_commit(wrong_path, e2e_harness.repo)

    raw_path = e2e_harness.write_model_action(raw, "2001")
    with pytest.raises(SystemExit):
        commit_agent_changes(e2e_harness.repo, raw_path, "2001")


def test_create_idea_checkpoint_pollution_is_rejected(
    e2e_harness: E2EHarness,
) -> None:
    _prepare_active_problem(e2e_harness, stage=LifecycleStage.IDEA_EVALUATION)
    control = e2e_harness.copy_control("checkpoint-control")
    raw = ActionEnvelope.model_validate(_idea_payload())
    materialized = ActionExecutor(e2e_harness.repo).prepare(raw)
    for change in materialized.files:
        target = e2e_harness.repo / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content, encoding="utf-8")
    old_checkpoint = e2e_harness.read_checkpoint()
    e2e_harness.write_checkpoint(
        old_checkpoint.model_copy(
            update={
                "idempotency_keys": [*old_checkpoint.idempotency_keys, "a" * 64],
                "updated_at": APPLIED_AT,
            }
        )
    )
    contract = validate_changed_file_contract(
        "agent/2002-create-idea-candidates",
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {
                "filename": f"research/ideas/{PROBLEM_ID}.json",
                "status": "added",
            },
        ],
    )
    valid = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=e2e_harness.repo,
        contract=contract,
    )
    assert valid.status == "valid"

    polluted = e2e_harness.read_checkpoint().model_copy(
        update={"last_signal_ids": ["signal-invented"]}
    )
    e2e_harness.write_checkpoint(polluted)
    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=e2e_harness.repo,
        contract=contract,
    )
    assert result.status == "invalid_checkpoint_change"

    e2e_harness.write_checkpoint(
        old_checkpoint.model_copy(
            update={
                "idempotency_keys": [*old_checkpoint.idempotency_keys, "b" * 64],
                "last_metrics_hash": "c" * 64,
                "updated_at": APPLIED_AT,
            }
        )
    )
    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=e2e_harness.repo,
        contract=contract,
    )
    assert result.status == "invalid_checkpoint_change"


def test_disallowed_state_transition_is_rejected_before_apply(
    e2e_harness: E2EHarness,
) -> None:
    action = _base_payload(ActionType.VALIDATE_EVIDENCE)
    action.update(
        {
            "evidence_ids": ["signal-001"],
            "state_transition": {
                "from": LifecycleStage.DISCOVERY.value,
                "to": LifecycleStage.IDEA_EVALUATION.value,
            },
        }
    )
    outcome = orchestrator.validate_model_action(
        e2e_harness.repo,
        CompanyState(lifecycle_stage=LifecycleStage.DISCOVERY),
        ActionEnvelope.model_validate(action),
    )
    assert outcome.diagnostic.rejection_code == ActionRejectionCode.INVALID_STATE_TRANSITION


def test_prompt_variant_accepts_evidence_and_idea_diagnostics() -> None:
    from agents.github_models import PromptVariant

    variant = PromptVariant(
        messages=[{"role": "user", "content": "{}"}],
        active_problem_id=PROBLEM_ID,
        candidate_evidence_id_count=2,
        resolved_evidence_count=2,
        unresolved_evidence_ids=[],
        new_signal_count=0,
        problem_loaded=True,
        problem_evidence_count=2,
        existing_idea_candidate_count=0,
        idea_context_ready=True,
        included_signal_count=2,
        allowed_evidence_ids=list(SIGNAL_IDS),
    )

    assert variant.idea_context_ready
    assert variant.allowed_evidence_ids == list(SIGNAL_IDS)


def test_materialized_create_idea_rejects_state_transition() -> None:
    raw = ActionEnvelope.model_validate(_idea_payload())
    with pytest.raises(ValidationError, match="cannot provide state_transition"):
        MaterializedActionEnvelope.from_model_action(
            raw,
            files=[
                {
                    "path": f"research/ideas/{PROBLEM_ID}.json",
                    "content": "{}\n",
                    "operation": "upsert",
                }
            ],
            state_transition=StateTransition.model_validate(
                {
                    "from": LifecycleStage.IDEA_EVALUATION.value,
                    "to": LifecycleStage.DISTRIBUTION_CHECK.value,
                }
            ),
        )
