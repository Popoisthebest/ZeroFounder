import json

import httpx
import pytest
from pydantic import ValidationError

from agents.github_models import GitHubModelsClient, parse_action_response
from agents.schemas import ActionType
from agents.usage_limiter import UsageLimiter

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


def test_parse_json_and_fenced_json():
    assert parse_action_response(json.dumps(VALID)).action_type == ActionType.NO_OP
    assert (
        parse_action_response(f"```json\n{json.dumps(VALID)}\n```").action_type == ActionType.NO_OP
    )


def test_invalid_json_and_extra_fields_rejected():
    with pytest.raises(json.JSONDecodeError):
        parse_action_response("not-json")
    invalid = dict(VALID, shell="echo unsafe")
    with pytest.raises(ValidationError):
        parse_action_response(json.dumps(invalid))


def test_catalog_model_fallback():
    client = GitHubModelsClient(
        "fake",
        UsageLimiter(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )
    catalog = [
        {
            "id": "vendor/text",
            "supported_input_modalities": ["text"],
            "supported_output_modalities": ["text"],
        }
    ]
    assert client.select_chat_model(catalog) == "vendor/text"


def test_schema_failure_uses_single_json_fallback():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(422, json={"message": "unsupported response_format"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    client = GitHubModelsClient(
        "fake",
        UsageLimiter(daily_limit=8),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    result = client.chat_action(
        model="vendor/text", messages=[{"role": "user", "content": "return json"}]
    )
    assert result.action_type == ActionType.NO_OP
    assert calls == 2


def test_embedding_failure_is_nonfatal():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"message": "down"}))
    client = GitHubModelsClient("fake", UsageLimiter(), transport=transport)
    assert client.embeddings(model="vendor/embed", texts=["idea"]) is None
