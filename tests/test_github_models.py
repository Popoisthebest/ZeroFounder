import json

import httpx
import pytest
from pydantic import ValidationError

from agents.github_models import (
    CHAT_URL,
    GitHubModelsClient,
    PromptVariant,
    extract_known_action_type,
    model_input_budget,
    parse_action_response,
)
from agents.schemas import (
    ActionRejectionCode,
    ActionType,
    CompactDiscoveryActionEnvelope,
    DiscoveryActionEnvelope,
    FailureStage,
    LifecycleStage,
    MessageContentType,
    ModelActionDiagnostic,
    ModelRequestMode,
)
from agents.usage_limiter import UsageLimiter
from scripts.write_model_summary import render_summary

VALID = {
    "role": "auditor",
    "action_type": "no_op",
    "title": "No action",
    "summary": "No material change",
    "rationale": "Preflight found no safe work",
    "risk_level": "low",
    "requires_approval": False,
    "evidence_ids": [],
    "files": [],
}

VALID_PROBLEM = {
    "role": "researcher",
    "action_type": "create_problem_candidate",
    "title": "Create a problem candidate",
    "summary": "Create one evidence-backed problem candidate.",
    "rationale": "Stored signals show a repeated manual workflow.",
    "risk_level": "low",
    "requires_approval": False,
    "evidence_ids": ["signal-001"],
    "problem_candidate": {
        "problem_id": "problem-001",
        "title": "Repeated manual coordination work",
        "target_users": ["small teams"],
        "description": "Small teams repeatedly reconcile the same coordination details by hand.",
        "current_workaround": "They combine spreadsheets, messages, and screenshots.",
    },
    "state_transition": {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
}

DISCOVERY_NO_OP = {key: value for key, value in VALID.items() if key != "files"}


def _completion(
    content: object = None,
    *,
    finish_reason: str | None = "stop",
    message: dict | None = None,
) -> dict:
    if message is None:
        message = {"content": json.dumps(VALID) if content is None else content}
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _client(handler, *, daily_limit: int = 8) -> GitHubModelsClient:
    return GitHubModelsClient(
        "fake",
        UsageLimiter(daily_limit=daily_limit),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def _single_response(payload: object):
    return lambda request: httpx.Response(200, json=payload)


def _run(client: GitHubModelsClient, **kwargs):
    return client.chat_action(
        model="vendor/text",
        messages=[{"role": "user", "content": "return json"}],
        **kwargs,
    )


def test_parse_json_and_fenced_json():
    assert parse_action_response(json.dumps(VALID)).action_type == ActionType.NO_OP
    assert (
        parse_action_response(f"```json\n{json.dumps(VALID)}\n```").action_type
        == ActionType.NO_OP
    )


def test_invalid_json_and_extra_fields_rejected():
    with pytest.raises(json.JSONDecodeError):
        parse_action_response("not-json")
    invalid = dict(VALID, shell="echo unsafe")
    with pytest.raises(ValidationError):
        parse_action_response(json.dumps(invalid))


def test_only_known_action_type_is_extracted_from_rejected_response():
    assert extract_known_action_type(json.dumps(VALID)) == ActionType.NO_OP
    assert extract_known_action_type('{"action_type":"run_shell"}') is None
    assert extract_known_action_type("not-json") is None


def test_model_selection_requires_chat_text_and_defaults_to_json_only(monkeypatch):
    monkeypatch.delenv("GITHUB_MODEL", raising=False)
    client = _client(_single_response([]))
    catalog = [
        {
            "id": "vendor/text",
            "supported_input_modalities": ["text"],
            "supported_output_modalities": ["text"],
        },
        {
            "id": "vendor/embed",
            "supported_input_modalities": ["text"],
            "supported_output_modalities": ["text"],
            "capabilities": ["embeddings"],
        },
    ]
    selection = client.select_chat_model(catalog)
    assert selection is not None
    assert selection.selected_model == "vendor/text"
    assert selection.request_mode == ModelRequestMode.JSON_ONLY


def test_known_structured_output_capability_selects_json_schema(monkeypatch):
    monkeypatch.delenv("GITHUB_MODEL", raising=False)
    client = _client(_single_response([]))
    selection = client.select_chat_model(
        [
            {
                "id": "vendor/structured",
                "supported_input_modalities": ["text"],
                "supported_output_modalities": ["text"],
                "capabilities": ["structured-output"],
                "supported_endpoints": ["inference/chat/completions"],
            }
        ]
    )
    assert selection is not None
    assert selection.request_mode == ModelRequestMode.JSON_SCHEMA


def test_model_selection_uses_catalog_input_limit_and_excludes_small_models(monkeypatch):
    monkeypatch.delenv("GITHUB_MODEL", raising=False)
    monkeypatch.setenv("GITHUB_FALLBACK_MODELS", "vendor/small,vendor/large")
    client = _client(_single_response([]))
    selection = client.select_chat_model(
        [
            {
                "id": "vendor/small",
                "supported_input_modalities": ["text"],
                "supported_output_modalities": ["text"],
                "limits": {"max_input_tokens": 2000},
            },
            {
                "id": "vendor/large",
                "supported_input_modalities": ["text"],
                "supported_output_modalities": ["text"],
                "limits": {"max_input_tokens": 16000},
            },
        ],
        required_input_tokens=3000,
    )
    assert selection is not None
    assert selection.selected_model == "vendor/large"
    assert selection.max_input_tokens == 16000
    assert selection.applied_input_budget == model_input_budget(16000)


def test_non_chat_endpoint_is_not_selected(monkeypatch):
    monkeypatch.delenv("GITHUB_MODEL", raising=False)
    client = _client(_single_response([]))
    selection = client.select_chat_model(
        [
            {
                "id": "vendor/generate",
                "supported_input_modalities": ["text"],
                "supported_output_modalities": ["text"],
                "supported_endpoints": ["images/generations"],
            }
        ]
    )
    assert selection is None


def test_string_content_parses_normal_no_op():
    client = _client(_single_response(_completion()))
    result = _run(client)
    assert result.action.action_type == ActionType.NO_OP
    assert result.rejection_code is None
    assert result.diagnostic.message_content_type == MessageContentType.STRING
    assert result.diagnostic.finish_reason == "stop"
    assert result.diagnostic.choices_count == 1
    assert result.diagnostic.response_char_count > 0
    assert result.diagnostic.completed_inference_calls == 1
    assert client.limiter.today().inference_calls == 1


def test_array_content_joins_only_text_items():
    content = [
        {"type": "text", "text": json.dumps(VALID)},
        {"type": "image", "url": "https://untrusted.example/image"},
    ]
    result = _run(_client(_single_response(_completion(content))))
    assert result.action.action_type == ActionType.NO_OP
    assert result.diagnostic.message_content_type == MessageContentType.ARRAY


def test_empty_choices_is_diagnosed_without_index_error():
    result = _run(_client(_single_response({"choices": []})))
    assert result.rejection_code == ActionRejectionCode.MODEL_RESPONSE_REJECTED
    assert result.diagnostic.failure_stage == FailureStage.CHOICE_EXTRACTION
    assert result.diagnostic.choices_count == 0
    assert result.diagnostic.retry_attempted


def test_missing_message_is_content_extraction_failure():
    result = _run(
        _client(_single_response({"choices": [{"finish_reason": "stop"}]}))
    )
    assert result.diagnostic.failure_stage == FailureStage.CONTENT_EXTRACTION
    assert result.diagnostic.message_content_type == MessageContentType.MISSING


def test_null_content_is_diagnosed():
    result = _run(_client(_single_response(_completion(message={"content": None}))))
    assert result.diagnostic.failure_stage == FailureStage.CONTENT_EXTRACTION
    assert result.diagnostic.message_content_type == MessageContentType.NULL
    assert result.diagnostic.response_char_count == 0


def test_refusal_is_rejected_without_retry():
    payload = _completion(message={"content": None, "refusal": "cannot comply"})
    result = _run(_client(_single_response(payload)))
    assert result.rejection_code == ActionRejectionCode.MODEL_REFUSAL
    assert result.diagnostic.failure_stage == FailureStage.CONTENT_EXTRACTION
    assert not result.diagnostic.retry_attempted


def test_content_filter_has_distinct_rejection_code():
    result = _run(
        _client(_single_response(_completion(finish_reason="content_filter")))
    )
    assert result.rejection_code == ActionRejectionCode.MODEL_CONTENT_FILTERED
    assert result.diagnostic.failure_stage == FailureStage.FINISH_REASON_CHECK
    assert result.diagnostic.finish_reason == "content_filter"


def test_finish_reason_length_is_truncated_without_fallback_or_retry():
    result = _run(
        _client(_single_response(_completion(finish_reason="length"))),
        request_mode=ModelRequestMode.JSON_SCHEMA,
    )
    assert result.rejection_code == ActionRejectionCode.TRUNCATED_MODEL_RESPONSE
    assert result.diagnostic.failure_stage == FailureStage.FINISH_REASON_CHECK
    assert not result.diagnostic.fallback_attempted
    assert not result.diagnostic.retry_attempted


def test_http_200_invalid_json_reports_json_parse_after_one_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion("{invalid"))

    result = _run(_client(handler))
    assert result.rejection_code == ActionRejectionCode.MODEL_RESPONSE_REJECTED
    assert result.diagnostic.failure_stage == FailureStage.JSON_PARSE
    assert result.diagnostic.http_status == 200
    assert result.diagnostic.retry_attempted
    assert calls == 2


@pytest.mark.parametrize("unsupported_status", [400, 422])
def test_json_schema_failure_falls_back_to_json_only_and_preserves_model_id(
    unsupported_status: int,
):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CHAT_URL
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "vendor/text"
        if len(calls) == 1:
            return httpx.Response(
                unsupported_status,
                json={"message": "unsupported response_format"},
            )
        return httpx.Response(200, json=_completion(json.dumps(DISCOVERY_NO_OP)))

    result = _run(
        _client(handler),
        request_mode=ModelRequestMode.JSON_SCHEMA,
    )
    assert result.action.action_type == ActionType.NO_OP
    assert result.rejection_code is None
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert result.diagnostic.request_mode == ModelRequestMode.JSON_ONLY
    assert result.diagnostic.fallback_attempted
    assert not result.diagnostic.retry_attempted
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.failed_after_request_calls == 1


def test_empty_structured_content_can_fall_back_to_json_only():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=_completion(""))
        return httpx.Response(200, json=_completion())

    result = _run(
        _client(handler),
        request_mode=ModelRequestMode.JSON_SCHEMA,
    )
    assert result.rejection_code is None
    assert result.diagnostic.fallback_attempted
    assert result.diagnostic.request_mode == ModelRequestMode.JSON_ONLY
    assert calls == 2


def test_pydantic_missing_fields_reports_only_field_paths():
    content = json.dumps({"role": "auditor", "action_type": "no_op"})
    result = _run(_client(_single_response(_completion(content))))
    assert result.diagnostic.failure_stage == FailureStage.SCHEMA_VALIDATION
    assert result.original_action_type == ActionType.NO_OP
    assert "title" in result.diagnostic.pydantic_validation_error_paths
    assert "summary" in result.diagnostic.pydantic_validation_error_paths
    assert content not in result.rejection_reason


def test_normal_create_problem_candidate_response():
    result = _run(
        _client(_single_response(_completion(json.dumps(VALID_PROBLEM))))
    )
    assert result.action.action_type == ActionType.CREATE_PROBLEM_CANDIDATE
    assert result.original_action_type == ActionType.CREATE_PROBLEM_CANDIDATE
    assert result.rejection_code is None


def test_schema_failure_correction_retry_succeeds_without_replaying_model_text():
    requests: list[dict] = []
    invalid = {key: value for key, value in VALID_PROBLEM.items() if key != "problem_candidate"}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        content = invalid if len(requests) == 1 else VALID_PROBLEM
        return httpx.Response(200, json=_completion(json.dumps(content)))

    result = _run(
        _client(handler),
        response_model=DiscoveryActionEnvelope,
    )
    assert result.rejection_code is None
    assert result.action.action_type == ActionType.CREATE_PROBLEM_CANDIDATE
    assert result.diagnostic.validation_correction_attempted
    assert result.diagnostic.retry_attempted
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.response_validation_failed_calls == 1
    correction = requests[1]["messages"][-1]["content"]
    assert "problem_candidate" in correction
    assert json.dumps(invalid) not in correction


def test_second_schema_failure_returns_safe_no_op_with_diagnostics():
    invalid = {key: value for key, value in VALID_PROBLEM.items() if key != "problem_candidate"}
    client = _client(_single_response(_completion(json.dumps(invalid))))
    result = _run(client, response_model=DiscoveryActionEnvelope)
    assert result.action.action_type == ActionType.NO_OP
    assert result.rejection_code == ActionRejectionCode.MODEL_RESPONSE_REJECTED
    assert result.diagnostic.failure_stage == FailureStage.SCHEMA_VALIDATION
    assert result.diagnostic.pydantic_validation_error_count >= 1
    assert any(
        error.missing_field == "problem_candidate"
        for error in result.diagnostic.pydantic_validation_errors
    )
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.http_failed_calls == 0
    assert result.diagnostic.response_validation_failed_calls == 2


def test_actions_summary_lists_safe_pydantic_error_details():
    invalid = {key: value for key, value in VALID_PROBLEM.items() if key != "problem_candidate"}
    result = _run(
        _client(_single_response(_completion(json.dumps(invalid)))),
        response_model=DiscoveryActionEnvelope,
    )
    diagnostic = ModelActionDiagnostic(
        lifecycle_stage=LifecycleStage.DISCOVERY,
        allowed_action_types=[ActionType.CREATE_PROBLEM_CANDIDATE, ActionType.NO_OP],
        original_action_type=result.original_action_type,
        validated_action_type=ActionType.NO_OP,
        accepted=False,
        rejection_code=result.rejection_code,
        rejection_reason=result.rejection_reason,
        inference=result.diagnostic,
    )
    summary = render_summary(diagnostic)
    assert "problem_candidate" in summary
    assert "missing" in summary
    assert "pydantic_validation_error_count" in summary
    assert json.dumps(invalid) not in summary


def test_diagnostic_mode_builds_small_exact_response_request():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_completion())

    result = _run(_client(handler), diagnostic_mode=True)
    assert result.rejection_code is None
    assert captured["max_tokens"] == 500
    assert any("Pipeline diagnostic mode" in item["content"] for item in captured["messages"])
    assert result.diagnostic.completed_inference_calls == 1
    assert result.diagnostic.request_body_bytes > 0
    assert result.diagnostic.estimated_input_tokens < result.diagnostic.applied_input_budget


def test_diagnostic_mode_never_sends_a_fallback_request():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, json={"message": "unsupported"})

    result = _run(
        _client(handler),
        diagnostic_mode=True,
        request_mode=ModelRequestMode.JSON_SCHEMA,
    )
    assert calls == 1
    assert result.diagnostic.completed_inference_calls == 1
    assert not result.diagnostic.fallback_attempted


def test_input_budget_blocks_before_http_request():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion())

    result = _client(handler).chat_action(
        model="vendor/text",
        messages=[{"role": "user", "content": "x" * 4000}],
        applied_input_budget=100,
        model_max_input_tokens=1000,
    )
    assert calls == 0
    assert result.rejection_code == ActionRejectionCode.INPUT_BUDGET_EXCEEDED
    assert result.diagnostic.completed_inference_calls == 0
    assert result.diagnostic.failure_stage == FailureStage.REQUEST_BUILD


def test_http_413_retries_once_with_compact_payload_and_succeeds():
    bodies: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(len(request.content))
        if len(bodies) == 1:
            return httpx.Response(413, json={"message": "too large"})
        return httpx.Response(200, json=_completion(json.dumps(DISCOVERY_NO_OP)))

    client = _client(handler)
    compact = PromptVariant(
        messages=[{"role": "user", "content": "compact"}],
        response_model=CompactDiscoveryActionEnvelope,
        context_chars=7,
        included_signal_count=6,
        excluded_signal_count=44,
    )
    result = client.chat_action(
        model="vendor/text",
        messages=[{"role": "user", "content": "standard " * 200}],
        response_model=DiscoveryActionEnvelope,
        compact_variant=compact,
        context_chars=1800,
        included_signal_count=12,
        excluded_signal_count=38,
        applied_input_budget=6000,
        model_max_input_tokens=16000,
    )
    assert result.rejection_code is None
    assert len(bodies) == 2
    assert bodies[1] < bodies[0]
    assert result.diagnostic.compact_retry_attempted
    assert result.diagnostic.included_signal_count == 6
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.failed_after_request_calls == 1


def test_second_http_413_finishes_as_request_too_large():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(413, json={"message": "too large"})

    client = _client(handler)
    result = client.chat_action(
        model="vendor/text",
        messages=[{"role": "user", "content": "standard"}],
        response_model=DiscoveryActionEnvelope,
        compact_variant=PromptVariant(
            messages=[{"role": "user", "content": "compact"}],
            response_model=CompactDiscoveryActionEnvelope,
        ),
        applied_input_budget=6000,
        model_max_input_tokens=16000,
    )
    assert calls == 2
    assert result.rejection_code == ActionRejectionCode.REQUEST_TOO_LARGE
    assert result.rejection_reason == "request_too_large"
    assert result.diagnostic.http_status == 413
    assert result.diagnostic.completed_inference_calls == 2
    assert result.diagnostic.failed_after_request_calls == 2


def test_unexpected_workflow_exception_releases_reservation():
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("workflow interrupted")

    client = _client(handler)
    with pytest.raises(RuntimeError, match="workflow interrupted"):
        _run(client)
    assert client.limiter.today().reserved_inference_calls == 0
    assert client.limiter.today().inference_calls == 1
    assert client.limiter.today().failed_after_request_calls == 1


def test_embedding_failure_is_nonfatal():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "down"})
    )
    client = GitHubModelsClient("fake", UsageLimiter(), transport=transport)
    assert client.embeddings(model="vendor/embed", texts=["idea"]) is None
