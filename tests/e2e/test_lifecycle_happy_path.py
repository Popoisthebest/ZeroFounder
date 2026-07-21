from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

import agents.orchestrator as orchestrator
from agents.context_builder import build_context_bundle
from agents.lifecycle import ALLOWED_TRANSITIONS, STAGE_ACTIONS, allowed_actions
from agents.safety import path_allowed_for_action
from agents.schemas import (
    ActionType,
    AgentRole,
    LifecycleStage,
    ModelCallResult,
    ModelInferenceDiagnostic,
    ModelRequestMode,
    ModelSelection,
    RiskLevel,
)
from tests.e2e.conftest import (
    PROBLEM_ID,
    SIGNAL_IDS,
    E2EHarness,
    idea_candidate,
    strategy_payload,
)


def _base_action(action_type: ActionType, title: str) -> dict[str, object]:
    return {
        "role": AgentRole.RESEARCHER.value,
        "action_type": action_type.value,
        "title": title,
        "summary": f"{title} for the current lifecycle stage.",
        "rationale": "The repository context contains the required prior artifact.",
        "risk_level": RiskLevel.LOW.value,
        "requires_approval": False,
        "evidence_ids": [],
    }


def _problem_action() -> dict[str, object]:
    payload = _base_action(ActionType.CREATE_PROBLEM_CANDIDATE, "Create problem candidate")
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "problem_candidate": {
                "problem_id": PROBLEM_ID,
                "title": "Repeated manual navigation",
                "target_users": ["operators"],
                "description": "Operators repeatedly lose position in long operational lists.",
                "current_workaround": "They scroll, search, and manually remember positions.",
            },
            "state_transition": {
                "from": LifecycleStage.DISCOVERY.value,
                "to": LifecycleStage.EVIDENCE_VALIDATION.value,
            },
        }
    )
    return payload


def _validate_evidence_action() -> dict[str, object]:
    payload = _base_action(ActionType.VALIDATE_EVIDENCE, "Validate stored evidence")
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "state_transition": {
                "from": LifecycleStage.EVIDENCE_VALIDATION.value,
                "to": LifecycleStage.IDEA_EVALUATION.value,
            },
        }
    )
    return payload


def _create_ideas_action() -> dict[str, object]:
    payload = _base_action(ActionType.CREATE_IDEA_CANDIDATES, "Create idea candidates")
    payload.update(
        {
            "evidence_ids": list(SIGNAL_IDS),
            "idea_candidates": [
                idea_candidate("idea-001", ["signal-001"]),
                idea_candidate("idea-002", list(SIGNAL_IDS)),
            ],
        }
    )
    return payload


def _file_action(
    action_type: ActionType,
    title: str,
    *,
    path: str,
    content: str,
    from_stage: LifecycleStage,
    to_stage: LifecycleStage,
    evidence_ids: list[str] | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = _base_action(action_type, title)
    payload.update(
        {
            "evidence_ids": evidence_ids or [],
            "files": [{"path": path, "content": content, "operation": "upsert"}],
            "state_transition": {"from": from_stage.value, "to": to_stage.value},
        }
    )
    if extra:
        payload.update(extra)
    return payload


def _evaluate_ideas_action() -> dict[str, object]:
    return _file_action(
        ActionType.EVALUATE_IDEAS,
        "Evaluate idea candidates",
        path=f"ideas/evaluations/{PROBLEM_ID}.json",
        content=json.dumps(
            {
                "problem_id": PROBLEM_ID,
                "selected": "idea-001",
                "evidence_ids": list(SIGNAL_IDS),
            },
            indent=2,
        )
        + "\n",
        from_stage=LifecycleStage.IDEA_EVALUATION,
        to_stage=LifecycleStage.DISTRIBUTION_CHECK,
        evidence_ids=list(SIGNAL_IDS),
        extra={"idea_candidate_ids": ["idea-001", "idea-002"]},
    )


def _remaining_stage_actions() -> dict[LifecycleStage, dict[str, object]]:
    return {
        LifecycleStage.DISTRIBUTION_CHECK: _file_action(
            ActionType.CHECK_DISTRIBUTION,
            "Check distribution",
            path="founder/outreach-plan.md",
            content="# Outreach Plan\n\nUse direct founder-led outreach.\n",
            from_stage=LifecycleStage.DISTRIBUTION_CHECK,
            to_stage=LifecycleStage.IDEA_SELECTED,
        ),
        LifecycleStage.IDEA_SELECTED: _file_action(
            ActionType.SELECT_IDEA,
            "Select idea",
            path="ideas/selected/decision.md",
            content="# Selected Idea\n\nSelected idea-001 using stored evidence.\n",
            from_stage=LifecycleStage.IDEA_SELECTED,
            to_stage=LifecycleStage.MVP_PLANNING,
            evidence_ids=["signal-001"],
        ),
        LifecycleStage.MVP_PLANNING: _file_action(
            ActionType.CREATE_PRODUCT_SPEC,
            "Create product spec",
            path="venture/product-requirements.md",
            content="# Product Requirements\n\nBuild saved list positions and jump controls.\n",
            from_stage=LifecycleStage.MVP_PLANNING,
            to_stage=LifecycleStage.INFRASTRUCTURE_SELECTION,
        ),
        LifecycleStage.INFRASTRUCTURE_SELECTION: _file_action(
            ActionType.SELECT_INFRASTRUCTURE,
            "Select infrastructure",
            path="venture/infrastructure.json",
            content=json.dumps({"provider": "github_pages"}, indent=2) + "\n",
            from_stage=LifecycleStage.INFRASTRUCTURE_SELECTION,
            to_stage=LifecycleStage.MVP_BUILDING,
        ),
        LifecycleStage.MVP_BUILDING: _file_action(
            ActionType.CREATE_CODE_PATCH,
            "Create code patch",
            path="venture/product/src/App.tsx",
            content="export default function App() { return <main>Navigation helper</main>; }\n",
            from_stage=LifecycleStage.MVP_BUILDING,
            to_stage=LifecycleStage.PRE_LAUNCH,
        ),
        LifecycleStage.PRE_LAUNCH: _file_action(
            ActionType.CREATE_CONTENT,
            "Create launch content",
            path="venture/content/launch.md",
            content="# Launch\n\nExplain the list navigation workflow.\n",
            from_stage=LifecycleStage.PRE_LAUNCH,
            to_stage=LifecycleStage.DISTRIBUTION_REQUIRED,
        ),
        LifecycleStage.DISTRIBUTION_REQUIRED: _file_action(
            ActionType.CREATE_EXPERIMENT,
            "Create distribution experiment",
            path="experiments/distribution-001.json",
            content=json.dumps({"experiment_id": "distribution-001"}, indent=2) + "\n",
            from_stage=LifecycleStage.DISTRIBUTION_REQUIRED,
            to_stage=LifecycleStage.VALIDATION_RUNNING,
        ),
        LifecycleStage.VALIDATION_RUNNING: _file_action(
            ActionType.RECORD_VALIDATION,
            "Record validation",
            path="reports/validation-running.md",
            content="# Validation\n\nRecorded early usage observations.\n",
            from_stage=LifecycleStage.VALIDATION_RUNNING,
            to_stage=LifecycleStage.OPERATING,
        ),
        LifecycleStage.OPERATING: _file_action(
            ActionType.CREATE_EXPERIMENT,
            "Create growth experiment",
            path="experiments/growth-001.json",
            content=json.dumps({"experiment_id": "growth-001"}, indent=2) + "\n",
            from_stage=LifecycleStage.OPERATING,
            to_stage=LifecycleStage.GROWTH_EXPERIMENT,
        ),
        LifecycleStage.GROWTH_EXPERIMENT: _file_action(
            ActionType.RECORD_VALIDATION,
            "Record growth validation",
            path="reports/growth-experiment.md",
            content="# Growth Experiment\n\nRecorded experiment observations.\n",
            from_stage=LifecycleStage.GROWTH_EXPERIMENT,
            to_stage=LifecycleStage.PIVOT_REVIEW,
        ),
        LifecycleStage.PIVOT_REVIEW: _file_action(
            ActionType.RECOMMEND_PIVOT,
            "Recommend pivot",
            path="reports/pivot-review.md",
            content="# Pivot Review\n\nRecommend narrowing the workflow focus.\n",
            from_stage=LifecycleStage.PIVOT_REVIEW,
            to_stage=LifecycleStage.PIVOTING,
        ),
        LifecycleStage.PIVOTING: _file_action(
            ActionType.UPDATE_STRATEGY,
            "Update strategy",
            path="company/strategy.json",
            content=json.dumps(strategy_payload(), indent=2) + "\n",
            from_stage=LifecycleStage.PIVOTING,
            to_stage=LifecycleStage.DISCOVERY,
        ),
    }


def _install_scripted_model(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, Any], dict[str, Any]], dict[str, object]],
    captured: list[dict[str, Any]],
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
            assert required_input_tokens <= 6000
            return ModelSelection(
                selected_model="fake/model",
                request_mode=ModelRequestMode.JSON_ONLY,
                max_input_tokens=16000,
                applied_input_budget=6000,
            )

        def chat_action(self, **kwargs: Any) -> ModelCallResult:
            policy = json.loads(kwargs["messages"][1]["content"])["orchestration_policy"]
            context = json.loads(kwargs["messages"][2]["content"])
            captured.append({"policy": policy, "context": context})
            response_model = kwargs["response_model"]
            parsed = response_model.model_validate(factory(policy, context))
            action = (
                parsed.to_action_envelope()
                if hasattr(parsed, "to_action_envelope")
                else parsed
            )
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
                excluded_signal_count=kwargs["excluded_signal_count"],
                completed_inference_calls=1,
            )
            return ModelCallResult(action=action, diagnostic=diagnostic)

    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeGitHubModelsClient)


def test_lifecycle_contract_matrix_is_derived_from_production_code() -> None:
    rows = []
    for stage in LifecycleStage:
        rows.append(
            {
                "stage": stage.value,
                "allowed_actions": [action.value for action in allowed_actions(stage)],
                "transition_targets": [
                    target.value for target in sorted(ALLOWED_TRANSITIONS[stage])
                ],
            }
        )

    assert [row["stage"] for row in rows] == [stage.value for stage in LifecycleStage]
    assert rows[0]["stage"] == LifecycleStage.DISCOVERY.value
    assert ActionType.CREATE_PROBLEM_CANDIDATE.value in rows[0]["allowed_actions"]
    assert LifecycleStage.PAUSED.value in rows[-1]["transition_targets"]
    for stage, actions in STAGE_ACTIONS.items():
        assert set(actions).issubset(set(allowed_actions(stage)))


def test_full_lifecycle_happy_path_uses_production_entrypoints(
    e2e_harness: E2EHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    remaining = _remaining_stage_actions()

    def action_factory(policy: dict[str, Any], context: dict[str, Any]) -> dict[str, object]:
        stage = LifecycleStage(policy["lifecycle_stage"])
        preferred = policy["preferred_action_types"]
        if stage == LifecycleStage.DISCOVERY:
            assert preferred[0] == ActionType.CREATE_PROBLEM_CANDIDATE.value
            return _problem_action()
        if stage == LifecycleStage.EVIDENCE_VALIDATION:
            assert policy["new_signal_ids"] == []
            assert policy["candidate_evidence_id_count"] == 2
            assert policy["resolved_evidence_count"] == 2
            return _validate_evidence_action()
        if stage == LifecycleStage.IDEA_EVALUATION:
            assert context["active_problem_id"] == PROBLEM_ID
            assert context["idea_stats"]["idea_context_ready"] is True
            if policy["existing_idea_candidate_count"] == 0:
                assert preferred[0] == ActionType.CREATE_IDEA_CANDIDATES.value
                return _create_ideas_action()
            assert preferred[0] == ActionType.EVALUATE_IDEAS.value
            return _evaluate_ideas_action()
        return remaining[stage]

    _install_scripted_model(monkeypatch, action_factory, captured)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    run_id = 1000

    def run_step(decision) -> object:
        nonlocal run_id
        run_id += 1
        run_id_text = str(run_id)
        outcome = orchestrator.run_model(e2e_harness.repo, decision)
        assert outcome.diagnostic.accepted, outcome.diagnostic.rejection_reason
        action_path = e2e_harness.write_model_action(outcome.action, run_id_text)
        preflight_path = e2e_harness.write_preflight(decision, run_id_text)
        return e2e_harness.apply_commit_validate(
            action_path=action_path,
            preflight_path=preflight_path,
            run_id=run_id_text,
            minute_offset=run_id - 1000,
        )

    problem_step = run_step(e2e_harness.discovery_decision())
    assert problem_step.raw_action.files == []
    assert problem_step.new_state.lifecycle_stage == LifecycleStage.EVIDENCE_VALIDATION
    assert problem_step.new_state.active_problem_id == PROBLEM_ID
    assert problem_step.changed_files == [
        "company/checkpoints.json",
        "company/state.json",
        f"research/problems/{PROBLEM_ID}.json",
    ]
    assert set(SIGNAL_IDS).issubset(set(problem_step.new_checkpoint.last_signal_ids))
    assert problem_step.old_checkpoint.idempotency_keys == []
    assert len(problem_step.new_checkpoint.idempotency_keys) == 1

    evidence_context = build_context_bundle(
        e2e_harness.repo,
        lifecycle_stage=LifecycleStage.EVIDENCE_VALIDATION,
        new_signal_ids=[],
    )
    assert evidence_context.candidate_evidence_id_count == 2
    assert evidence_context.resolved_evidence_count == 2

    evidence_step = run_step(e2e_harness.manual_decision(str(run_id + 1)))
    assert evidence_step.new_state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION
    assert evidence_step.new_state.active_problem_id == PROBLEM_ID
    assert evidence_step.changed_files == ["company/checkpoints.json", "company/state.json"]
    assert (
        evidence_step.new_checkpoint.last_signal_ids
        == evidence_step.old_checkpoint.last_signal_ids
    )

    idea_context = build_context_bundle(
        e2e_harness.repo,
        lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
    )
    assert idea_context.idea_context_ready is True
    assert idea_context.existing_idea_candidate_count == 0
    assert idea_context.included_signal_count == 2

    idea_step = run_step(e2e_harness.manual_decision(str(run_id + 1)))
    assert idea_step.raw_action.files == []
    assert idea_step.raw_action.state_transition is None
    assert idea_step.action.files[0].path == f"research/ideas/{PROBLEM_ID}.json"
    assert idea_step.new_state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION
    assert idea_step.new_checkpoint.last_signal_ids == idea_step.old_checkpoint.last_signal_ids
    assert idea_step.new_checkpoint.last_metrics_hash == idea_step.old_checkpoint.last_metrics_hash
    assert len(idea_step.new_checkpoint.idempotency_keys) == (
        len(idea_step.old_checkpoint.idempotency_keys) + 1
    )

    ideas = json.loads(
        (e2e_harness.repo / f"research/ideas/{PROBLEM_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["idea_id"] for item in ideas["idea_candidates"]] == [
        "idea-001",
        "idea-002",
    ]

    evaluation_context = build_context_bundle(
        e2e_harness.repo,
        lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
    )
    assert evaluation_context.existing_idea_candidate_count == 2
    evaluation_step = run_step(e2e_harness.manual_decision(str(run_id + 1)))
    assert evaluation_step.raw_action.action_type == ActionType.EVALUATE_IDEAS
    assert evaluation_step.new_state.lifecycle_stage == LifecycleStage.DISTRIBUTION_CHECK
    assert f"research/ideas/{PROBLEM_ID}.json" not in evaluation_step.changed_files

    expected_stages = [
        LifecycleStage.DISTRIBUTION_CHECK,
        LifecycleStage.IDEA_SELECTED,
        LifecycleStage.MVP_PLANNING,
        LifecycleStage.INFRASTRUCTURE_SELECTION,
        LifecycleStage.MVP_BUILDING,
        LifecycleStage.PRE_LAUNCH,
        LifecycleStage.DISTRIBUTION_REQUIRED,
        LifecycleStage.VALIDATION_RUNNING,
        LifecycleStage.OPERATING,
        LifecycleStage.GROWTH_EXPERIMENT,
        LifecycleStage.PIVOT_REVIEW,
        LifecycleStage.PIVOTING,
    ]
    visited = []
    for expected_stage in expected_stages:
        assert e2e_harness.read_state().lifecycle_stage == expected_stage
        step = run_step(e2e_harness.manual_decision(str(run_id + 1)))
        visited.append(step.old_state.lifecycle_stage)
        assert step.raw_action.action_type in allowed_actions(expected_stage)
        for change in step.raw_action.files:
            assert path_allowed_for_action(change.path, step.raw_action.action_type)
        assert step.validation.status == "valid"
        assert step.quality_result["validation_status"] == "passed"

    assert visited == expected_stages
    assert e2e_harness.read_state().lifecycle_stage == LifecycleStage.DISCOVERY
    assert len(e2e_harness.push_records()) == run_id - 1000
    assert {entry["policy"]["lifecycle_stage"] for entry in captured}.issuperset(
        {stage.value for stage in expected_stages}
    )
