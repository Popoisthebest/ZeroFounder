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
        _action("create_idea_candidates"),
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
        _action("create_idea_candidates"),
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
        "resolved_evidence_count",
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
    assert variant.resolved_evidence_count == 0
    assert variant.unresolved_evidence_ids == []
    assert variant.new_signal_count == 0
    assert variant.included_signal_count == 0
