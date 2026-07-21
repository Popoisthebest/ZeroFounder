from __future__ import annotations

import json
from typing import Any

import httpx

import agents.orchestrator as orchestrator
from agents.github_models import GitHubModelsClient
from agents.schemas import ActionType, CompanyState, LifecycleStage
from agents.usage_limiter import UsageLimiter
from tests.e2e.conftest import (
    PROBLEM_ID,
    SIGNAL_IDS,
    active_problem_payload,
    idea_candidate,
    write_json,
)


def _completion(content: object) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _valid_ideas() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "create_idea_candidates",
        "title": "Create idea candidates",
        "summary": "Generate evidence-backed idea candidates.",
        "rationale": "The active problem has validated evidence.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "idea_candidates": [
            idea_candidate("idea-001", ["signal-001"]),
            idea_candidate("idea-002", list(SIGNAL_IDS)),
        ],
    }


def _evaluate_ideas_action() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "evaluate_ideas",
        "title": "Evaluate idea candidates",
        "summary": "Evaluate existing candidates for the active validated problem.",
        "rationale": "Two stored candidates exist, so evaluation is the next step.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "idea_candidate_ids": ["idea-001", "idea-002"],
        "files": [
            {
                "path": f"ideas/evaluations/{PROBLEM_ID}.json",
                "content": json.dumps(
                    {
                        "problem_id": PROBLEM_ID,
                        "selected": "idea-001",
                        "evidence_ids": list(SIGNAL_IDS),
                    },
                    indent=2,
                )
                + "\n",
                "operation": "upsert",
            }
        ],
        "state_transition": {
            "from": "IDEA_EVALUATION",
            "to": "DISTRIBUTION_CHECK",
        },
    }


def _large_idea_context() -> dict[str, Any]:
    return {
        "lifecycle_stage": "IDEA_EVALUATION",
        "mission": "mission " * 1200,
        "safety_constraints": "safety " * 1200,
        "active_problem_id": PROBLEM_ID,
        "active_problem": {
            "problem_id": PROBLEM_ID,
            "title": "Repeated manual navigation",
            "description": "Operators repeatedly lose position in long lists. "
            + "detail " * 900,
            "target_users": ["operators"],
            "evidence_ids": list(SIGNAL_IDS),
        },
        "existing_idea_candidates": [],
        "included_signal_records": [
            {
                "signal_id": "signal-001",
                "title": "Repeated list navigation",
                "summary": "Operators repeatedly return to the same list rows. "
                + "x " * 900,
            },
            {
                "signal_id": "signal-002",
                "title": "Manual position tracking",
                "summary": "Teams manually remember row positions while working. "
                + "y " * 900,
            },
        ],
        "validation_metadata": "metadata " * 1200,
    }


def test_initial_idea_prompt_is_minimized_before_http_and_correction_stays_in_budget() -> None:
    requests: list[dict[str, Any]] = []
    invalid = _valid_ideas()
    invalid["files"] = [
        {
            "path": f"research/ideas/{PROBLEM_ID}.json",
            "content": "{}\n",
            "operation": "upsert",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json=_completion(json.dumps(invalid)))
        return httpx.Response(200, json=_completion(json.dumps(_valid_ideas())))

    client = GitHubModelsClient(
        "fake-token",
        UsageLimiter(daily_limit=8),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    context = _large_idea_context()
    result = client.chat_action(
        model="vendor/text",
        messages=[
            {"role": "system", "content": "Return one JSON object."},
            {"role": "user", "content": json.dumps(context)},
        ],
        active_problem_id=PROBLEM_ID,
        problem_loaded=True,
        problem_evidence_count=2,
        resolved_evidence_count=2,
        idea_context_ready=True,
        existing_idea_candidate_count=0,
        included_signal_count=2,
        allowed_evidence_ids=list(SIGNAL_IDS),
        applied_input_budget=6000,
        model_max_input_tokens=16000,
    )

    assert result.rejection_code is None
    assert result.action.action_type == ActionType.CREATE_IDEA_CANDIDATES
    assert len(requests) == 2
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.response_validation_failed_calls == 1
    assert result.diagnostic.reserved_correction_tokens == 1000
    assert result.diagnostic.initial_target_tokens == 5000
    assert result.diagnostic.initial_estimated_tokens <= 5000
    assert result.diagnostic.correction_target_tokens == 6000
    assert result.diagnostic.correction_estimated_tokens <= 6000
    assert result.diagnostic.compacted_context
    assert {"mission", "full_signal_records", "unused_action_type_schema"}.issubset(
        set(result.diagnostic.removed_context_sections)
    )

    initial_prompt = "\n".join(item["content"] for item in requests[0]["messages"])
    correction_prompt = "\n".join(item["content"] for item in requests[1]["messages"])
    assert PROBLEM_ID in initial_prompt
    assert "Repeated manual navigation" in initial_prompt
    assert "signal-001" in initial_prompt
    assert "signal-002" in initial_prompt
    assert "mission mission" not in initial_prompt
    assert "validation_metadata" not in initial_prompt
    assert len(correction_prompt) < len(json.dumps(context))
    assert "allowed_evidence_ids" in correction_prompt


def test_request_over_budget_fails_before_http_when_no_safe_minimization_exists() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion(json.dumps(_valid_ideas())))

    client = GitHubModelsClient(
        "fake-token",
        UsageLimiter(daily_limit=8),
        transport=httpx.MockTransport(handler),
    )
    result = client.chat_action(
        model="vendor/text",
        messages=[{"role": "user", "content": "oversized " * 5000}],
        applied_input_budget=200,
        model_max_input_tokens=1000,
    )

    assert calls == 0
    assert result.rejection_code is not None
    assert result.diagnostic.completed_inference_calls == 0


def test_e2e_default_model_selection_reaches_evaluate_ideas_http_call(
    e2e_harness,
    monkeypatch,
) -> None:
    requests: list[dict[str, Any]] = []

    write_json(e2e_harness.repo / f"research/problems/{PROBLEM_ID}.json", active_problem_payload())
    write_json(
        e2e_harness.repo / f"research/ideas/{PROBLEM_ID}.json",
        {
            "problem_id": PROBLEM_ID,
            "idea_candidates": [
                idea_candidate("idea-001", ["signal-001"]),
                idea_candidate("idea-002", list(SIGNAL_IDS)),
            ],
        },
    )
    e2e_harness.write_state(
        CompanyState(
            lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
            active_problem_id=PROBLEM_ID,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(json.dumps(_evaluate_ideas_action())))

    class FakeGitHubModelsClient(GitHubModelsClient):
        def __init__(self, token: str, limiter: UsageLimiter) -> None:
            super().__init__(
                token,
                limiter,
                transport=httpx.MockTransport(handler),
                sleep=lambda _: None,
            )

        def catalog(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "cohere/cohere-command-a",
                    "supported_input_modalities": ["text"],
                    "supported_output_modalities": ["text"],
                    "supported_endpoints": ["inference/chat/completions"],
                    "limits": {"context_window": 131072},
                }
            ]

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_MODEL", "")
    monkeypatch.setenv("GITHUB_FALLBACK_MODELS", "")
    monkeypatch.setenv("MAX_MODEL_INPUT_TOKENS", "6000")
    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeGitHubModelsClient)

    outcome = orchestrator.run_model(
        e2e_harness.repo,
        e2e_harness.manual_decision("evaluate-ideas"),
    )

    assert outcome.diagnostic.accepted
    assert outcome.action.action_type == ActionType.EVALUATE_IDEAS
    assert outcome.diagnostic.inference.completed_inference_calls == 1
    assert outcome.diagnostic.inference.active_problem_id == PROBLEM_ID
    assert outcome.diagnostic.inference.existing_idea_candidate_count == 2
    assert outcome.diagnostic.inference.selected_model_source == "default"
    assert outcome.diagnostic.inference.required_input_tokens == 6000
    assert outcome.diagnostic.inference.evaluated_model_candidates
    assert requests
    assert requests[0]["model"] == "cohere/cohere-command-a"
    prompt = "\n".join(message["content"] for message in requests[0]["messages"])
    assert PROBLEM_ID in prompt
    assert "evaluate_ideas" in prompt
