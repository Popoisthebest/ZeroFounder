from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from agents.schemas import ActionEnvelope, ActionType, AgentRole, RiskLevel
from agents.usage_limiter import UsageLimiter, UsageLimitReached, request_fingerprint

MODELS_BASE = "https://models.github.ai"
CATALOG_URL = f"{MODELS_BASE}/catalog/models"
CHAT_URL = f"{MODELS_BASE}/inference/chat/completions"
EMBEDDINGS_URL = f"{MODELS_BASE}/inference/embeddings"
API_VERSION = "2026-03-10"
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S | re.I)
TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.I),
)


def mask_secrets(value: str) -> str:
    for pattern in TOKEN_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def strip_markdown_fence(value: str) -> str:
    match = FENCE.fullmatch(value)
    return match.group(1).strip() if match else value.strip()


def parse_action_response(value: str) -> ActionEnvelope:
    raw = strip_markdown_fence(value)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be one JSON object")
    return ActionEnvelope.model_validate(parsed)


def safe_no_op(reason: str) -> ActionEnvelope:
    return ActionEnvelope(
        role=AgentRole.AUDITOR,
        action_type=ActionType.NO_OP,
        title="안전한 종료",
        summary=mask_secrets(reason)[:1000] or "No action",
        rationale="모델 응답 또는 사용 조건이 안전 기준을 충족하지 못했습니다.",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
    )


class GitHubModelsClient:
    def __init__(
        self,
        token: str,
        limiter: UsageLimiter,
        *,
        timeout: float = 45.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token
        self.limiter = limiter
        self.sleep = sleep
        self.client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "Content-Type": "application/json",
            },
        )

    def catalog(self) -> list[dict[str, Any]]:
        response = self.client.get(CATALOG_URL)
        response.raise_for_status()
        self.limiter.record_catalog()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _is_text_model(model: dict[str, Any]) -> bool:
        inputs = model.get("supported_input_modalities", ["text"])
        outputs = model.get("supported_output_modalities", ["text"])
        return "text" in inputs and "text" in outputs and isinstance(model.get("id"), str)

    @staticmethod
    def _is_embedding_model(model: dict[str, Any]) -> bool:
        tags = {str(tag).lower() for tag in model.get("tags", [])}
        capabilities = {str(item).lower() for item in model.get("capabilities", [])}
        model_id = str(model.get("id", "")).lower()
        return "embedding" in model_id or "embeddings" in tags | capabilities

    def select_chat_model(self, catalog: list[dict[str, Any]]) -> str | None:
        available = {model["id"]: model for model in catalog if self._is_text_model(model)}
        configured = os.getenv("GITHUB_MODEL")
        fallbacks = [
            item.strip()
            for item in os.getenv(
                "GITHUB_FALLBACK_MODELS", "openai/gpt-4.1-mini,openai/gpt-4.1"
            ).split(",")
            if item.strip()
        ]
        for candidate in ([configured] if configured else []) + fallbacks:
            if candidate in available:
                return candidate
        return next(iter(available), None)

    def select_embedding_model(self, catalog: list[dict[str, Any]]) -> str | None:
        configured = os.getenv("GITHUB_EMBEDDING_MODEL")
        models = [model for model in catalog if self._is_embedding_model(model)]
        if configured and any(model.get("id") == configured for model in models):
            return configured
        return str(models[0]["id"]) if models else None

    def chat_action(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_output_chars: int | None = None,
    ) -> ActionEnvelope:
        max_output_chars = max_output_chars or int(os.getenv("MAX_TOTAL_OUTPUT_CHARS", "60000"))
        base_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 6000,
            "stream": False,
        }
        attempts = 0
        use_schema = True
        last_error = "unknown model error"
        while attempts < 2:
            payload = dict(base_payload)
            if use_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "zerofounder_action",
                        "strict": True,
                        "schema": ActionEnvelope.model_json_schema(),
                    },
                }
            else:
                payload["messages"] = messages + [
                    {
                        "role": "system",
                        "content": (
                            "Return exactly one JSON object matching the supplied schema. "
                            "No Markdown."
                        ),
                    }
                ]
            fingerprint = request_fingerprint({"kind": "chat", "payload": payload})
            try:
                self.limiter.reserve("chat", fingerprint)
                attempts += 1
                response = self.client.post(CHAT_URL, json=payload)
                if response.status_code in {400, 422} and use_schema and attempts < 2:
                    use_schema = False
                    continue
                if response.status_code in {429} or response.status_code >= 500:
                    last_error = f"GitHub Models transient error {response.status_code}"
                    self.limiter.record_failure()
                    if attempts < 2:
                        self.sleep(2 ** (attempts - 1))
                        continue
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or len(content) > max_output_chars:
                    raise ValueError("model output length is invalid")
                return parse_action_response(content)
            except (
                UsageLimitReached,
                httpx.HTTPError,
                KeyError,
                IndexError,
                ValueError,
                ValidationError,
            ) as exc:
                last_error = str(exc)
                self.limiter.record_failure()
                if isinstance(exc, UsageLimitReached) or attempts >= 2:
                    break
                self.sleep(2 ** max(attempts - 1, 0))
        return safe_no_op(last_error)

    def embeddings(self, *, model: str, texts: list[str]) -> list[list[float]] | None:
        if not texts or len(texts) > 64:
            return None
        payload = {
            "model": model,
            "input": [text[:8000] for text in texts],
            "encoding_format": "float",
        }
        fingerprint = request_fingerprint({"kind": "embedding", "payload": payload})
        try:
            self.limiter.reserve("embedding", fingerprint)
            response = self.client.post(EMBEDDINGS_URL, json=payload)
            response.raise_for_status()
            data = response.json().get("data", [])
            vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
            if len(vectors) != len(texts):
                return None
            return vectors
        except (UsageLimitReached, httpx.HTTPError, KeyError, TypeError, ValueError):
            self.limiter.record_failure()
            return None
