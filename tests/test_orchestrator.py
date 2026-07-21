import json
from pathlib import Path

import agents.orchestrator as orchestrator
from agents.context_builder import build_context
from agents.github_models import PromptVariant
from agents.orchestrator import build_model_instruction, validate_model_action
from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    CompanyState,
    LifecycleStage,
    ModelCallResult,
    ModelInferenceDiagnostic,
    ModelRequestMode,
    ModelSelection,
    PreflightDecision,
)
from scripts.write_model_summary import render_summary


def _write_strategy(root: Path, minimum: int = 3) -> None:
    target = root / "company"
    target.mkdir(parents=True, exist_ok=True)
    (target / "strategy.json").write_text(
        json.dumps({"evidence": {"min_unique_signals": minimum}})
    )


def _write_signals(root: Path, count: int = 3) -> list[str]:
    target = root / "signals/raw"
    target.mkdir(parents=True, exist_ok=True)
    ids = [f"signal-{index:03d}" for index in range(count)]
    records = [
        {
            "signal_id": signal_id,
            "source_pack": "productivity",
            "source_type": "rss",
            "url": f"https://example.test/{signal_id}",
            "title": f"Problem {signal_id}",
            "summary": "A repeated manual workflow problem.",
            "collected_at": "2026-07-20T00:00:00Z",
            "published_at": "2026-07-19T00:00:00Z",
            "content_hash": signal_id.replace("signal", "hash"),
        }
        for signal_id in ids
    ]
    (target / "signals.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    return ids


def _decision(ids: list[str]) -> PreflightDecision:
    return PreflightDecision.model_validate(
        {
            "should_call_model": True,
            "reasons": ["new_signals"],
            "new_signal_ids": ids,
            "idempotency_key": "a" * 64,
        }
    )


def _action(action_type: str, **overrides) -> ActionEnvelope:
    payload = {
        "role": "researcher",
        "action_type": action_type,
        "title": "Discovery action",
        "summary": "Analyze existing evidence safely.",
        "rationale": "Stored market signals are available.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": [],
        "files": [],
    }
    payload.update(overrides)
    return ActionEnvelope.model_validate(payload)


def _idea_candidates() -> list[dict[str, object]]:
    return [
        {
            "idea_id": "idea-001",
            "name": "List jump helper",
            "summary": "긴 목록에서 반복 탐색을 줄이는 점프형 조작 도구입니다.",
            "target_users": ["operators"],
            "proposed_solution": "사용자가 검증된 간격으로 목록을 이동하고 위치를 보존합니다.",
            "value_proposition": "반복 스크롤과 수동 위치 기억을 줄여 작업 흐름을 단순화합니다.",
            "differentiation": "대시보드가 아니라 기존 목록 조작의 마찰을 직접 줄입니다.",
            "revenue_model": "팀 단위 고급 설정을 유료화할 수 있습니다.",
            "feasibility": "정적 MVP에서 목록 상태와 단축 조작만 구현하면 됩니다.",
            "evidence_ids": ["signal-000", "signal-001"],
            "risks": ["기존 단축키 습관을 바꾸지 않을 수 있습니다."],
            "evaluation_dimensions": ["반복 사용 가능성", "무료 MVP 구현성"],
        },
        {
            "idea_id": "idea-002",
            "name": "Saved list positions",
            "summary": "반복 작업 위치를 저장해 긴 목록 재탐색을 줄이는 도구입니다.",
            "target_users": ["operators"],
            "proposed_solution": "사용자가 작업 묶음별 위치와 필터를 저장하고 다시 엽니다.",
            "value_proposition": "같은 목록 위치를 매번 다시 찾는 시간을 줄입니다.",
            "differentiation": "검색 결과보다 작업 맥락의 복귀 지점을 보존합니다.",
            "revenue_model": "공유 위치 묶음 기능을 유료 팀 기능으로 확장할 수 있습니다.",
            "feasibility": "브라우저 저장소 기반의 정적 MVP로 검증할 수 있습니다.",
            "evidence_ids": ["signal-000"],
            "risks": ["사용자가 저장 위치를 관리하는 부담을 느낄 수 있습니다."],
            "evaluation_dimensions": ["전환 비용", "작업 빈도"],
        },
    ]


def test_discovery_prompt_prefers_problem_creation_after_signal_collection(tmp_path: Path):
    _write_strategy(tmp_path)
    ids = _write_signals(tmp_path)
    instruction = json.loads(
        build_model_instruction(tmp_path, CompanyState(), _decision(ids))
    )["orchestration_policy"]
    assert set(instruction["allowed_action_types"]) == {
        "collect_signals",
        "create_problem_candidate",
        "validate_evidence",
        "write_report",
        "no_op",
    }
    assert instruction["preferred_action_types"][0] == "create_problem_candidate"
    assert instruction["preferred_action_types"][-1] == "collect_signals"
    assert instruction["repository_counts"]["raw_signals"] == 3


def test_discovery_prompt_prefers_validation_when_problem_exists(tmp_path: Path):
    _write_strategy(tmp_path)
    ids = _write_signals(tmp_path)
    problems = tmp_path / "research/problems"
    problems.mkdir(parents=True)
    (problems / "problem-001.json").write_text("{}")
    instruction = json.loads(
        build_model_instruction(tmp_path, CompanyState(), _decision(ids))
    )["orchestration_policy"]
    assert instruction["preferred_action_types"][0] == "validate_evidence"


def test_recent_signals_are_in_model_context(tmp_path: Path):
    _write_strategy(tmp_path)
    ids = _write_signals(tmp_path)
    context = json.loads(build_context(tmp_path))
    included = {item["signal_id"] for item in context["representative_signals"]}
    assert included == set(ids)


def test_disallowed_discovery_action_becomes_diagnostic_no_op(tmp_path: Path):
    outcome = validate_model_action(
        tmp_path,
        CompanyState(),
        _action("create_idea_candidates", idea_candidates=_idea_candidates()),
    )
    assert outcome.action.action_type.value == "no_op"
    assert outcome.diagnostic.original_action_type.value == "create_idea_candidates"
    assert outcome.diagnostic.rejection_code == (
        ActionRejectionCode.LIFECYCLE_ACTION_NOT_ALLOWED
    )


def test_allowed_discovery_evidence_action_is_preserved(tmp_path: Path):
    ids = _write_signals(tmp_path, 1)
    outcome = validate_model_action(
        tmp_path,
        CompanyState(),
        _action(
            "create_problem_candidate",
            evidence_ids=ids,
            problem_candidate={
                "problem_id": "problem-001",
                "title": "Repeated manual coordination",
                "target_users": ["small teams"],
                "description": "Small teams repeatedly reconcile coordination details manually.",
                "current_workaround": "They use spreadsheets and message threads.",
            },
            state_transition={"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
        ),
    )
    assert outcome.action.action_type.value == "create_problem_candidate"
    assert outcome.diagnostic.accepted


def test_job_summary_masks_tokens_and_omits_model_text():
    diagnostic = validate_model_action(
        Path("."),
        CompanyState(),
        _action("no_op"),
    ).diagnostic
    diagnostic.rejection_reason = "blocked ghp_123456789012345678901234567890"
    summary = render_summary(diagnostic)
    assert "[REDACTED]" in summary
    assert "raw_model_text" not in summary
    assert "Authorization" not in summary
    for field in {
        "selected_model",
        "request_mode",
        "http_status",
        "choices_count",
        "message_content_type",
        "response_char_count",
        "finish_reason",
        "fallback_attempted",
        "retry_attempted",
        "request_body_bytes",
        "system_prompt_chars",
        "user_prompt_chars",
        "schema_chars",
        "context_chars",
        "estimated_input_tokens",
        "selected_model_max_input_tokens",
        "applied_input_budget",
        "active_problem_id",
        "candidate_evidence_id_count",
        "problem_loaded",
        "problem_evidence_count",
        "resolved_evidence_count",
        "existing_idea_candidate_count",
        "idea_context_ready",
        "unresolved_evidence_ids",
        "new_signal_count",
        "included_signal_count",
        "excluded_signal_count",
        "failure_stage",
        "rejection_code",
        "pydantic_validation_error_paths",
    }:
        assert f"| {field} |" in summary


def test_run_model_diagnostics_include_evidence_validation_context(
    tmp_path: Path,
    monkeypatch,
):
    _write_strategy(tmp_path)
    _write_signals(tmp_path, 2)
    (tmp_path / "agents/prompts").mkdir(parents=True)
    (tmp_path / "agents/prompts/core.md").write_text("Return JSON.")
    (tmp_path / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.EVIDENCE_VALIDATION,
            active_problem_id="problem-001",
        ).model_dump_json()
    )
    (tmp_path / "research/problems").mkdir(parents=True)
    problem = {
        "problem_id": "problem-001",
        "title": "Repeated manual navigation",
        "target_users": ["operators"],
        "description": "Operators repeatedly navigate long lists manually.",
        "current_workaround": "They scroll and search by hand.",
        "evidence_ids": ["signal-000", "signal-001"],
        "evidence": [
            {
                "evidence_id": "signal-000",
                "source_type": "rss",
                "url": "https://example.test/signal-000",
                "summary": "A repeated manual workflow problem.",
            },
            {
                "evidence_id": "signal-001",
                "source_type": "rss",
                "url": "https://example.test/signal-001",
                "summary": "A second repeated manual workflow problem.",
            },
        ],
        "frequency_score": 5,
        "severity_score": 5,
        "buildability_score": 7,
        "confidence": 0.7,
    }
    (tmp_path / "research/problems/problem-001.json").write_text(json.dumps(problem))
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, token, limiter):
            pass

        def catalog(self):
            return [{"id": "fake/model"}]

        def select_chat_model(self, catalog, *, required_input_tokens):
            return ModelSelection(
                selected_model="fake/model",
                request_mode=ModelRequestMode.JSON_ONLY,
                max_input_tokens=8000,
                applied_input_budget=6000,
            )

        def chat_action(self, **kwargs):
            captured.update(kwargs)
            diagnostic = ModelInferenceDiagnostic(
                active_problem_id=kwargs["active_problem_id"],
                candidate_evidence_id_count=kwargs["candidate_evidence_id_count"],
                resolved_evidence_count=kwargs["resolved_evidence_count"],
                unresolved_evidence_ids=kwargs["unresolved_evidence_ids"],
                new_signal_count=kwargs["new_signal_count"],
                included_signal_count=kwargs["included_signal_count"],
            )
            return ModelCallResult(
                action=_action(
                    "validate_evidence",
                    evidence_ids=["signal-000", "signal-001"],
                    state_transition={
                        "from": "EVIDENCE_VALIDATION",
                        "to": "IDEA_EVALUATION",
                    },
                ),
                diagnostic=diagnostic,
            )

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeClient)

    outcome = orchestrator.run_model(tmp_path, _decision([]))

    assert outcome.diagnostic.accepted
    assert captured["active_problem_id"] == "problem-001"
    assert captured["candidate_evidence_id_count"] == 2
    assert captured["resolved_evidence_count"] == 2
    assert captured["unresolved_evidence_ids"] == []
    assert captured["new_signal_count"] == 0
    assert captured["included_signal_count"] == 2


def test_prompt_variant_defaults_support_non_evidence_lifecycle():
    variant = PromptVariant(messages=[{"role": "user", "content": "context"}])

    assert variant.active_problem_id is None
    assert variant.candidate_evidence_id_count == 0
    assert not variant.problem_loaded
    assert variant.problem_evidence_count == 0
    assert variant.resolved_evidence_count == 0
    assert variant.existing_idea_candidate_count == 0
    assert not variant.idea_context_ready
    assert variant.unresolved_evidence_ids == []
    assert variant.new_signal_count == 0
    assert variant.included_signal_count == 0


def _write_idea_evaluation_problem(root: Path) -> None:
    _write_strategy(root)
    _write_signals(root, 2)
    (root / "agents/prompts").mkdir(parents=True)
    (root / "agents/prompts/core.md").write_text("Return JSON.")
    (root / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
            active_problem_id="problem-001",
        ).model_dump_json()
    )
    (root / "research/problems").mkdir(parents=True)
    problem = {
        "problem_id": "problem-001",
        "title": "Repeated manual navigation",
        "target_users": ["operators"],
        "description": "Operators repeatedly navigate long lists manually.",
        "current_workaround": "They scroll and search by hand.",
        "evidence_ids": ["signal-000", "signal-001"],
        "evidence": [
            {
                "evidence_id": "signal-000",
                "source_type": "rss",
                "url": "https://example.test/signal-000",
                "summary": "A repeated manual workflow problem.",
            },
            {
                "evidence_id": "signal-001",
                "source_type": "rss",
                "url": "https://example.test/signal-001",
                "summary": "A second repeated manual workflow problem.",
            },
        ],
        "frequency_score": 5,
        "severity_score": 5,
        "buildability_score": 7,
        "confidence": 0.7,
    }
    (root / "research/problems/problem-001.json").write_text(json.dumps(problem))


def test_idea_evaluation_context_allows_idea_creation_without_new_signals(
    tmp_path: Path,
    monkeypatch,
):
    _write_idea_evaluation_problem(tmp_path)
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, token, limiter):
            pass

        def catalog(self):
            return [{"id": "fake/model"}]

        def select_chat_model(self, catalog, *, required_input_tokens):
            return ModelSelection(
                selected_model="fake/model",
                request_mode=ModelRequestMode.JSON_ONLY,
                max_input_tokens=8000,
                applied_input_budget=6000,
            )

        def chat_action(self, **kwargs):
            captured.update(kwargs)
            diagnostic = ModelInferenceDiagnostic(
                active_problem_id=kwargs["active_problem_id"],
                problem_loaded=kwargs["problem_loaded"],
                problem_evidence_count=kwargs["problem_evidence_count"],
                resolved_evidence_count=kwargs["resolved_evidence_count"],
                existing_idea_candidate_count=kwargs["existing_idea_candidate_count"],
                included_signal_count=kwargs["included_signal_count"],
                idea_context_ready=kwargs["idea_context_ready"],
            )
            return ModelCallResult(
                action=_action(
                    "create_idea_candidates",
                    evidence_ids=["signal-000", "signal-001"],
                    idea_candidates=_idea_candidates(),
                ),
                diagnostic=diagnostic,
            )

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeClient)

    outcome = orchestrator.run_model(tmp_path, _decision([]))

    assert outcome.diagnostic.accepted
    assert captured["active_problem_id"] == "problem-001"
    assert captured["problem_loaded"] is True
    assert captured["problem_evidence_count"] == 2
    assert captured["resolved_evidence_count"] == 2
    assert captured["existing_idea_candidate_count"] == 0
    assert captured["included_signal_count"] == 2
    assert captured["idea_context_ready"] is True
    instruction = json.loads(captured["messages"][1]["content"])["orchestration_policy"]
    prompt = json.loads(captured["messages"][2]["content"])
    assert instruction["preferred_action_types"][0] == "create_idea_candidates"
    assert instruction["new_signal_count"] == 0
    assert instruction["new_signal_ids"] == []
    assert prompt["active_problem_id"] == "problem-001"
    assert prompt["active_problem"]["title"] == "Repeated manual navigation"
    assert [item["signal_id"] for item in prompt["included_signal_records"]] == [
        "signal-000",
        "signal-001",
    ]


def test_idea_evaluation_missing_problem_record_is_rejected(
    tmp_path: Path,
    monkeypatch,
):
    _write_strategy(tmp_path)
    (tmp_path / "agents/prompts").mkdir(parents=True)
    (tmp_path / "agents/prompts/core.md").write_text("Return JSON.")
    (tmp_path / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
            active_problem_id="problem-001",
        ).model_dump_json()
    )

    class FakeClient:
        def __init__(self, token, limiter):
            pass

        def catalog(self):
            return [{"id": "fake/model"}]

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(orchestrator, "GitHubModelsClient", FakeClient)

    outcome = orchestrator.run_model(tmp_path, _decision([]))

    assert not outcome.diagnostic.accepted
    assert outcome.diagnostic.rejection_code.value == "missing_problem_record"
    assert outcome.diagnostic.inference.active_problem_id == "problem-001"
    assert not outcome.diagnostic.inference.problem_loaded
    assert not outcome.diagnostic.inference.idea_context_ready


def test_idea_evaluation_prefers_evaluation_when_candidates_exist(tmp_path: Path):
    _write_idea_evaluation_problem(tmp_path)
    (tmp_path / "research/ideas").mkdir(parents=True)
    (tmp_path / "research/ideas/problem-001.json").write_text(
        json.dumps(
            {
                "problem_id": "problem-001",
                "idea_candidates": [_idea_candidates()[0]],
            }
        )
    )

    instruction = json.loads(
        orchestrator.build_model_instruction(
            tmp_path,
            CompanyState(
                lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
                active_problem_id="problem-001",
            ),
            _decision([]),
        )
    )["orchestration_policy"]

    assert instruction["preferred_action_types"][0] == "evaluate_ideas"
    assert instruction["existing_idea_candidate_count"] == 1


def test_create_idea_candidates_rejects_unvalidated_evidence(tmp_path: Path):
    _write_idea_evaluation_problem(tmp_path)
    candidates = _idea_candidates()
    candidates[0]["evidence_ids"] = ["signal-invented"]
    action = _action(
        "create_idea_candidates",
        evidence_ids=["signal-000", "signal-001"],
        idea_candidates=candidates,
    )
    diagnostic = ModelInferenceDiagnostic(
        active_problem_id="problem-001",
        problem_loaded=True,
        problem_evidence_count=2,
        resolved_evidence_count=2,
        included_signal_count=2,
        idea_context_ready=True,
    )

    outcome = validate_model_action(
        tmp_path,
        CompanyState(
            lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
            active_problem_id="problem-001",
        ),
        action,
        diagnostic,
    )

    assert not outcome.diagnostic.accepted
    assert outcome.diagnostic.rejection_code == ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED
    assert outcome.diagnostic.inference.rejected_idea_candidate_count == 1
    assert outcome.diagnostic.inference.accepted_idea_candidate_count == 0
