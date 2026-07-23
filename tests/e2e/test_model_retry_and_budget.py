from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import agents.orchestrator as orchestrator
from agents.context_builder import build_context_bundle
from agents.github_models import GitHubModelsClient
from agents.report_materializer import report_artifact_path, report_period
from agents.schemas import ActionType, CompanyState, LifecycleStage
from agents.usage_limiter import UsageLimiter
from tests.e2e.conftest import (
    PROBLEM_ID,
    SIGNAL_IDS,
    active_problem_payload,
    idea_candidate,
    run_git,
    write_json,
)


def _completion(content: object) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _valid_ideas() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "create_idea_candidates",
        "title": "아이디어 후보 생성",
        "summary": "검증된 근거를 바탕으로 아이디어 후보를 생성합니다.",
        "rationale": "활성 문제에는 검증된 근거가 있어 후보 생성을 진행할 수 있습니다.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "idea_candidates": [
            {
                **idea_candidate("idea-001", ["signal-001"]),
                "name": "목록 점프 도우미",
                "summary": "반복 탐색 위치로 빠르게 돌아가는 도구입니다.",
                "target_users": ["운영 담당자"],
                "proposed_solution": "목록 위치를 저장하고 한 번에 이동하게 합니다.",
                "value_proposition": "반복 스크롤과 위치 기억 부담을 줄입니다.",
                "differentiation": "검색이 아니라 작업 위치 복귀에 집중합니다.",
                "revenue_model": "팀 공유 위치 묶음을 유료 기능으로 확장합니다.",
                "feasibility": "브라우저 저장소 기반 MVP로 빠르게 검증할 수 있습니다.",
                "risks": ["사용자가 기존 탐색 습관을 유지할 수 있습니다."],
                "evaluation_dimensions": ["반복 사용성", "구현 용이성"],
            },
            {
                **idea_candidate("idea-002", list(SIGNAL_IDS)),
                "name": "작업 위치 저장 도구",
                "summary": "작업별 위치와 필터를 저장해 재탐색을 줄입니다.",
                "target_users": ["운영 담당자"],
                "proposed_solution": "위치와 필터 묶음을 저장하고 다시 엽니다.",
                "value_proposition": "같은 목록 위치를 다시 찾는 시간을 줄입니다.",
                "differentiation": "검색 결과보다 작업 맥락 복귀를 보존합니다.",
                "revenue_model": "공유 위치 묶음을 유료 팀 기능으로 제공합니다.",
                "feasibility": "정적 MVP로 저장과 복귀 흐름을 검증할 수 있습니다.",
                "risks": ["저장 위치 관리가 부담이 될 수 있습니다."],
                "evaluation_dimensions": ["전환 비용", "작업 빈도"],
            },
        ],
    }


def _evaluate_ideas_action() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "evaluate_ideas",
        "title": "아이디어 후보 평가",
        "summary": "검증된 활성 문제에 대한 기존 후보를 평가합니다.",
        "rationale": "저장된 후보 두 개가 있으므로 다음 단계로 평가를 수행합니다.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "idea_candidate_ids": ["idea-001", "idea-002"],
        "idea_evaluations": [
            {
                "idea_id": "idea-001",
                "score": 8,
                "reason": "근거와 직접 연결되고 구현 범위가 작아 우선 평가 대상입니다.",
                "strengths": ["반복 탐색 문제를 직접 줄입니다.", "정적 MVP로 검증할 수 있습니다."],
                "risks": ["기존 습관을 바꾸기 어려울 수 있습니다."],
            },
            {
                "idea_id": "idea-002",
                "score": 7,
                "reason": "작업 맥락 복귀 가치는 크지만 저장 관리 부담이 있습니다.",
                "strengths": ["팀 단위 공유 기능으로 확장할 수 있습니다."],
                "risks": ["저장 위치가 많아지면 관리 비용이 생길 수 있습니다."],
            },
        ],
        "state_transition": {
            "from": "IDEA_EVALUATION",
            "to": "DISTRIBUTION_CHECK",
        },
    }


def _problem_action() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "create_problem_candidate",
        "title": "문제 후보 생성",
        "summary": "근거가 있는 문제 후보를 생성합니다.",
        "rationale": "축약된 신호에서 반복되는 수동 탐색 문제가 확인됐습니다.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "problem_candidate": {
            "problem_id": PROBLEM_ID,
            "title": "반복되는 수동 목록 탐색",
            "target_users": ["운영 담당자"],
            "description": "운영 담당자가 긴 목록에서 위치를 반복해서 잃습니다.",
            "current_workaround": "스크롤, 검색, 수동 기억을 조합합니다.",
        },
        "state_transition": {
            "from": "DISCOVERY",
            "to": "EVIDENCE_VALIDATION",
        },
    }


def _validate_evidence_action() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "validate_evidence",
        "title": "근거 검증",
        "summary": "활성 문제의 근거를 검증합니다.",
        "rationale": "저장된 두 근거가 모두 문제를 뒷받침합니다.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "state_transition": {
            "from": "EVIDENCE_VALIDATION",
            "to": "IDEA_EVALUATION",
        },
    }


def _write_report_action() -> dict[str, object]:
    return {
        "role": "researcher",
        "action_type": "write_report",
        "title": "보고서 작성",
        "summary": "근거가 있는 짧은 보고서를 작성합니다.",
        "rationale": "저장소에 짧은 보고서를 작성할 충분한 맥락이 있습니다.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": list(SIGNAL_IDS),
        "report": {
            "report_type": "weekly",
            "title": "주간 운영 보고서",
            "summary": "근거가 있는 짧은 운영 보고서를 작성합니다.",
            "period_summary": "요청 예산 회귀 테스트 기간의 핵심 흐름을 요약합니다.",
            "sections": [
                {
                    "heading": "핵심 판단",
                    "content": "저장된 근거를 바탕으로 다음 운영 판단을 정리합니다.",
                }
            ],
            "evidence_ids": list(SIGNAL_IDS),
        },
    }


def _invalid_for(action_type: str, valid: dict[str, object]) -> dict[str, object]:
    invalid = json.loads(json.dumps(valid))
    if action_type == "create_problem_candidate":
        invalid.pop("problem_candidate", None)
    elif action_type == "validate_evidence":
        invalid["evidence_ids"] = []
    elif action_type == "create_idea_candidates":
        invalid["files"] = [{"path": "research/ideas/bad.json", "content": "{}"}]
    elif action_type == "evaluate_ideas":
        invalid.pop("idea_candidate_ids", None)
        invalid.pop("idea_evaluations", None)
    elif action_type == "write_report":
        invalid.pop("report", None)
    return invalid


def _budget_context(action_type: str) -> dict[str, Any]:
    base_problem = active_problem_payload()
    evidence = [
        {
            "signal_id": signal_id,
            "title": f"Evidence {signal_id}",
            "summary": "Operators repeatedly lose list position." + " evidence" * 700,
        }
        for signal_id in SIGNAL_IDS
    ]
    payload: dict[str, Any] = {
        "required_action": action_type,
        "mission": "mission " * 1000,
        "safety_constraints": "safety " * 1000,
        "included_signal_records": evidence,
        "validation_metadata": "metadata " * 1000,
    }
    if action_type == "create_problem_candidate":
        payload.update(
            {
                "lifecycle_stage": "DISCOVERY",
                "representative_signals": evidence,
                "signal_clusters": [{"theme": "navigation", "details": "cluster " * 500}],
            }
        )
    elif action_type == "validate_evidence":
        payload.update(
            {
                "lifecycle_stage": "EVIDENCE_VALIDATION",
                "active_problem_id": PROBLEM_ID,
                "active_problem_candidate": base_problem,
                "candidate_evidence_ids": list(SIGNAL_IDS),
            }
        )
    elif action_type in {"create_idea_candidates", "evaluate_ideas"}:
        payload.update(
            {
                "lifecycle_stage": "IDEA_EVALUATION",
                "active_problem_id": PROBLEM_ID,
                "active_problem": base_problem,
                "existing_idea_candidates": (
                    [
                        {
                            **idea_candidate("idea-001", ["signal-001"]),
                            "summary": "Candidate one." + " compare" * 500,
                        },
                        {
                            **idea_candidate("idea-002", list(SIGNAL_IDS)),
                            "summary": "Candidate two." + " compare" * 500,
                        },
                    ]
                    if action_type == "evaluate_ideas"
                    else []
                ),
            }
        )
    else:
        payload.update(
            {
                "lifecycle_stage": "REPORTING",
                "active_problem_id": PROBLEM_ID,
                "report_target": "budget manager report " * 600,
            }
        )
    return payload


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


@pytest.mark.parametrize(
    ("action_type", "valid_action", "expected_removed", "must_keep"),
    [
        (
            "create_problem_candidate",
            _problem_action(),
            {"full_action_schema", "full_signal_records", "unrelated_lifecycle_instructions"},
            ["signal-001", "signal-002", "create_problem_candidate"],
        ),
        (
            "validate_evidence",
            _validate_evidence_action(),
            {"full_action_schema", "full_signal_records", "verbose_validation_metadata"},
            [PROBLEM_ID, "signal-001", "signal-002", "validate_evidence"],
        ),
        (
            "create_idea_candidates",
            _valid_ideas(),
            {"full_action_schema", "full_signal_records", "validation_metadata"},
            [PROBLEM_ID, "signal-001", "signal-002", "create_idea_candidates"],
        ),
        (
            "evaluate_ideas",
            _evaluate_ideas_action(),
            {"full_action_schema", "full_signal_records", "verbose_candidate_fields"},
            [PROBLEM_ID, "idea-001", "idea-002", "evaluate_ideas"],
        ),
        (
            "write_report",
            _write_report_action(),
            {"full_action_schema", "full_signal_records", "verbose_repository_metadata"},
            [PROBLEM_ID, "write_report"],
        ),
    ],
)
def test_request_budget_manager_compacts_each_action_and_correction_retry(
    action_type: str,
    valid_action: dict[str, object],
    expected_removed: set[str],
    must_keep: list[str],
) -> None:
    requests: list[dict[str, Any]] = []
    invalid_action = _invalid_for(action_type, valid_action)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        content = invalid_action if len(requests) == 1 else valid_action
        return httpx.Response(200, json=_completion(json.dumps(content)))

    existing_count = 2 if action_type == "evaluate_ideas" else 0
    result = GitHubModelsClient(
        "fake-token",
        UsageLimiter(daily_limit=8),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    ).chat_action(
        model="vendor/text",
        messages=[
            {"role": "system", "content": "Return one JSON object."},
            {"role": "user", "content": json.dumps(_budget_context(action_type))},
        ],
        active_problem_id=PROBLEM_ID,
        problem_loaded=action_type
        in {"validate_evidence", "create_idea_candidates", "evaluate_ideas"},
        problem_evidence_count=2,
        resolved_evidence_count=2,
        candidate_evidence_id_count=2,
        idea_context_ready=action_type in {"create_idea_candidates", "evaluate_ideas"},
        existing_idea_candidate_count=existing_count,
        included_signal_count=2,
        allowed_evidence_ids=list(SIGNAL_IDS),
        applied_input_budget=6000,
        model_max_input_tokens=16000,
    )

    assert result.rejection_code is None
    assert result.action.action_type.value == action_type
    assert len(requests) == 2
    assert result.diagnostic.initial_target_tokens == 5000
    assert result.diagnostic.initial_estimated_tokens <= 5000
    assert result.diagnostic.correction_target_tokens == 6000
    assert result.diagnostic.correction_estimated_tokens <= 6000
    assert result.diagnostic.validation_correction_attempted
    assert result.diagnostic.compacted_context
    assert expected_removed.issubset(set(result.diagnostic.removed_context_sections))

    initial_prompt = "\n".join(item["content"] for item in requests[0]["messages"])
    correction_prompt = "\n".join(item["content"] for item in requests[1]["messages"])
    for value in must_keep:
        assert value in initial_prompt
    assert "mission mission" not in initial_prompt
    assert "safety safety" not in initial_prompt
    assert "allowed_evidence_ids" in correction_prompt or action_type != "create_idea_candidates"
    schema_text = json.dumps(requests[0])
    if action_type == "evaluate_ideas":
        assert "create_idea_candidates" not in initial_prompt
        assert "problem_candidate" not in schema_text


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
    run_git(e2e_harness.repo, "add", "company/state.json", "research/problems", "research/ideas")
    run_git(e2e_harness.repo, "commit", "-m", "prepare idea evaluation fixture")

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

    run_id = "2001"
    action_path = e2e_harness.write_model_action(outcome.action, run_id)
    preflight_path = e2e_harness.write_preflight(
        e2e_harness.manual_decision(run_id),
        run_id,
    )
    step = e2e_harness.apply_commit_validate(
        action_path=action_path,
        preflight_path=preflight_path,
        run_id=run_id,
    )
    assert step.validation.status == "valid"
    assert step.quality_result["validation_status"] == "passed"
    assert step.new_state.lifecycle_stage == LifecycleStage.DISTRIBUTION_CHECK
    assert f"ideas/evaluations/{PROBLEM_ID}.json" in step.changed_files
    stored = json.loads(
        (e2e_harness.repo / f"ideas/evaluations/{PROBLEM_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["problem_id"] == PROBLEM_ID
    assert stored["idea_candidate_ids"] == ["idea-001", "idea-002"]
    assert "근거와 직접 연결" in stored["idea_evaluations"][0]["reason"]
    next_context = json.loads(
        build_context_bundle(
            e2e_harness.repo,
            lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK,
        ).content
    )
    assert next_context["recent_idea_evaluations"][0]["problem_id"] == PROBLEM_ID


def test_e2e_distribution_check_write_report_materializes_and_blocks_duplicate(
    e2e_harness,
    monkeypatch,
) -> None:
    requests: list[dict[str, Any]] = []
    e2e_harness.write_state(
        CompanyState(lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK)
    )
    run_git(e2e_harness.repo, "add", "company/state.json")
    run_git(e2e_harness.repo, "commit", "-m", "prepare distribution check fixture")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(json.dumps(_write_report_action())))

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
        e2e_harness.manual_decision("write-report"),
    )

    assert outcome.diagnostic.accepted
    assert outcome.action.action_type == ActionType.WRITE_REPORT
    assert outcome.action.files == []
    assert outcome.action.report is not None
    assert requests
    prompt = "\n".join(message["content"] for message in requests[0]["messages"])
    assert "write_report" in prompt
    assert "missing_file.txt" not in json.dumps(outcome.action.model_dump(mode="json"))

    run_id = "2002"
    action_path = e2e_harness.write_model_action(outcome.action, run_id)
    preflight_path = e2e_harness.write_preflight(
        e2e_harness.manual_decision(run_id),
        run_id,
    )
    step = e2e_harness.apply_commit_validate(
        action_path=action_path,
        preflight_path=preflight_path,
        run_id=run_id,
    )

    expected_report = report_artifact_path(report_period(e2e_harness.repo))
    assert step.changed_files == ["company/checkpoints.json", expected_report]
    assert step.action.files[0].path == expected_report
    assert step.validation.report_type == "weekly"
    assert step.validation.artifact_path == expected_report
    assert step.quality_result["artifact_path"] == expected_report
    assert (e2e_harness.repo / expected_report).read_bytes().startswith(b"%PDF-")

    decision = orchestrator.preflight(e2e_harness.repo, None, "schedule")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "idempotency_key_already_processed"
    assert decision["artifact_path"] == expected_report
    assert decision["operation_key"]
    assert step.new_state.lifecycle_stage == LifecycleStage.DISTRIBUTION_CHECK
