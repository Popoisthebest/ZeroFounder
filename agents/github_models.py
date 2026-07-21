from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    ActionType,
    AgentRole,
    FailureStage,
    MessageContentType,
    ModelCallResult,
    ModelInferenceDiagnostic,
    ModelRequestMode,
    ModelSelection,
    PydanticErrorDiagnostic,
    RiskLevel,
)
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
STRUCTURED_OUTPUT_CAPABILITIES = {
    "structured-output",
    "structured-outputs",
    "structured_output",
    "structured_outputs",
    "json-schema",
    "json_schema",
    "response-format-json-schema",
}
CHAT_ENDPOINT_NAMES = {
    "chat",
    "chat-completion",
    "chat-completions",
    "chat/completions",
    "inference/chat/completions",
}
DEFAULT_MODEL_MAX_INPUT_TOKENS = 8192
DEFAULT_MAX_MODEL_INPUT_TOKENS = 6000
CONSERVATIVE_FREE_INPUT_TOKENS = 6000
DEFAULT_MAX_INPUT_CHARS = 24_000
DIAGNOSTIC_ACTION = {
    "role": "auditor",
    "action_type": "no_op",
    "title": "진단",
    "summary": "모델 응답 처리 경로를 진단합니다.",
    "rationale": "모델 응답 파이프라인 검증을 위한 최소 응답입니다.",
    "risk_level": "low",
    "requires_approval": False,
    "evidence_ids": [],
    "state_transition": None,
    "files": [],
    "dependency_proposal": None,
}
DISCOVERY_CORRECTION_EXAMPLE = {
    "role": "researcher",
    "action_type": "create_problem_candidate",
    "title": "문제 후보 생성",
    "summary": "근거가 있는 문제 후보 하나를 생성합니다.",
    "rationale": "저장된 신호에서 같은 구체적 우회 방식이 확인됐습니다.",
    "risk_level": "low",
    "requires_approval": False,
    "evidence_ids": ["signal-existing-id"],
    "problem_candidate": {
        "problem_id": "problem-example",
        "title": "구체적으로 반복되는 문제",
        "target_users": ["명확한 사용자 집단"],
        "description": "저장된 근거가 뒷받침하는 구체적이고 반복적인 문제입니다.",
        "current_workaround": "사용자는 현재 수작업 단계와 기존 도구를 조합합니다.",
    },
    "state_transition": {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
}


@dataclass(frozen=True)
class PromptVariant:
    messages: list[dict[str, str]]
    response_model: type[BaseModel] = ActionEnvelope
    context_chars: int = 0
    active_problem_id: str | None = None
    candidate_evidence_id_count: int = 0
    resolved_evidence_count: int = 0
    unresolved_evidence_ids: list[str] = field(default_factory=list)
    new_signal_count: int = 0
    included_signal_count: int = 0
    excluded_signal_count: int = 0


def estimate_input_tokens(messages: list[dict[str, str]], schema_text: str, *,
                          schema_in_messages: bool) -> int:
    message_bytes = sum(len(item.get("content", "").encode("utf-8")) for item in messages)
    schema_bytes = 0 if schema_in_messages else len(schema_text.encode("utf-8"))
    return math.ceil((message_bytes + schema_bytes) / 3) + 32


def model_input_budget(max_input_tokens: int) -> int:
    configured_tokens = int(
        os.getenv("MAX_MODEL_INPUT_TOKENS", str(DEFAULT_MAX_MODEL_INPUT_TOKENS))
    )
    max_input_chars = int(os.getenv("MAX_INPUT_CHARS", str(DEFAULT_MAX_INPUT_CHARS)))
    chars_as_tokens = max(1, math.ceil(max_input_chars / 3))
    return max(
        1,
        min(
            max(1, math.floor(max_input_tokens * 0.6)),
            configured_tokens,
            CONSERVATIVE_FREE_INPUT_TOKENS,
            chars_as_tokens,
        ),
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


def extract_known_action_type(value: str) -> ActionType | None:
    try:
        parsed = json.loads(strip_markdown_fence(value))
        if not isinstance(parsed, dict):
            return None
        return ActionType(parsed.get("action_type"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _known_action_type_from_object(value: object) -> ActionType | None:
    if not isinstance(value, dict):
        return None
    try:
        return ActionType(value.get("action_type"))
    except (TypeError, ValueError):
        return None


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


class ModelPipelineError(RuntimeError):
    def __init__(
        self,
        stage: FailureStage,
        reason: str,
        *,
        code: ActionRejectionCode = ActionRejectionCode.MODEL_RESPONSE_REJECTED,
        retryable: bool = False,
        fallback_eligible: bool = False,
        validation_paths: list[str] | None = None,
        validation_errors: list[PydanticErrorDiagnostic] | None = None,
        original_action_type: ActionType | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.code = code
        self.retryable = retryable
        self.fallback_eligible = fallback_eligible
        self.validation_paths = validation_paths or []
        self.validation_errors = validation_errors or []
        self.original_action_type = original_action_type


def _expected_type(error_type: str) -> str | None:
    if error_type == "missing":
        return "required field"
    if error_type == "extra_forbidden":
        return "field must be omitted"
    if error_type == "literal_error":
        return "allowed literal"
    mappings = {
        "string_type": "string",
        "list_type": "array",
        "dict_type": "object",
        "int_type": "integer",
        "float_type": "number",
        "bool_type": "boolean",
    }
    return mappings.get(error_type)


def _validation_error_details(exc: ValidationError) -> list[PydanticErrorDiagnostic]:
    details: list[PydanticErrorDiagnostic] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = error.get("loc", ())
        path = ".".join(str(item) for item in location) or "<root>"
        error_type = str(error.get("type") or "validation_error")[:100]
        leaf = str(location[-1])[:200] if location else None
        details.append(
            PydanticErrorDiagnostic(
                path=path[:300],
                error_type=error_type,
                message=str(error.get("msg") or "validation failed")[:300],
                missing_field=leaf if error_type == "missing" else None,
                extra_field=leaf if error_type == "extra_forbidden" else None,
                expected_type=_expected_type(error_type),
            )
        )
    return details[:50]


def _extract_json_object(value: str) -> tuple[dict[str, Any], ActionType | None]:
    raw = strip_markdown_fence(value)
    start = raw.find("{")
    if start < 0:
        raise ModelPipelineError(
            FailureStage.JSON_EXTRACTION,
            "model content did not contain a JSON object",
            retryable=True,
            fallback_eligible=True,
        )
    candidate = raw[start:]
    try:
        parsed, end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ModelPipelineError(
            FailureStage.JSON_PARSE,
            "model content contained invalid JSON",
            retryable=True,
            fallback_eligible=True,
        ) from exc
    if candidate[end:].strip():
        raise ModelPipelineError(
            FailureStage.JSON_EXTRACTION,
            "model content contained data outside the JSON object",
            retryable=True,
            fallback_eligible=True,
            original_action_type=_known_action_type_from_object(parsed),
        )
    if not isinstance(parsed, dict):
        raise ModelPipelineError(
            FailureStage.JSON_PARSE,
            "model JSON must be one object",
            retryable=True,
            fallback_eligible=True,
        )
    return parsed, _known_action_type_from_object(parsed)


def _content_type(message: dict[str, Any]) -> MessageContentType:
    if "content" not in message:
        return MessageContentType.MISSING
    content = message["content"]
    if content is None:
        return MessageContentType.NULL
    if isinstance(content, str):
        return MessageContentType.STRING
    if isinstance(content, list):
        return MessageContentType.ARRAY
    return MessageContentType.OTHER


def _extract_text_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "output_text"} and isinstance(
                item.get("text"), str
            ):
                fragments.append(item["text"])
        return "".join(fragments)
    return ""


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
        model_id = model.get("id")
        inputs = model.get("supported_input_modalities")
        outputs = model.get("supported_output_modalities")
        if not isinstance(model_id, str) or "/" not in model_id:
            return False
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            return False
        if "text" not in inputs or "text" not in outputs:
            return False
        if GitHubModelsClient._is_embedding_model(model):
            return False
        endpoints = model.get("supported_endpoints", model.get("endpoints"))
        if isinstance(endpoints, list) and endpoints:
            normalized = {str(item).lower().strip("/") for item in endpoints}
            if not normalized.intersection(CHAT_ENDPOINT_NAMES):
                return False
        return True

    @staticmethod
    def _is_embedding_model(model: dict[str, Any]) -> bool:
        tags = {str(tag).lower() for tag in model.get("tags", [])}
        capabilities = {str(item).lower() for item in model.get("capabilities", [])}
        model_id = str(model.get("id", "")).lower()
        return "embedding" in model_id or "embeddings" in tags | capabilities

    @staticmethod
    def _supports_structured_output(model: dict[str, Any]) -> bool:
        capabilities = {str(item).lower() for item in model.get("capabilities", [])}
        return bool(capabilities.intersection(STRUCTURED_OUTPUT_CAPABILITIES))

    @staticmethod
    def _max_input_tokens(model: dict[str, Any]) -> int:
        limits = model.get("limits")
        value = limits.get("max_input_tokens") if isinstance(limits, dict) else None
        if value is None:
            value = model.get("max_input_tokens")
        if isinstance(value, int) and value > 0:
            return value
        return DEFAULT_MODEL_MAX_INPUT_TOKENS

    def select_chat_model(
        self,
        catalog: list[dict[str, Any]],
        *,
        required_input_tokens: int = 0,
    ) -> ModelSelection | None:
        available = {model["id"]: model for model in catalog if self._is_text_model(model)}
        configured = os.getenv("GITHUB_MODEL")
        fallbacks = [
            item.strip()
            for item in os.getenv(
                "GITHUB_FALLBACK_MODELS", "openai/gpt-4.1-mini,openai/gpt-4.1"
            ).split(",")
            if item.strip()
        ]
        candidates = ([configured] if configured else []) + fallbacks + sorted(available)
        for candidate in candidates:
            if candidate in available:
                max_input_tokens = self._max_input_tokens(available[candidate])
                input_budget = model_input_budget(max_input_tokens)
                if required_input_tokens > input_budget:
                    continue
                mode = (
                    ModelRequestMode.JSON_SCHEMA
                    if self._supports_structured_output(available[candidate])
                    else ModelRequestMode.JSON_ONLY
                )
                return ModelSelection(
                    selected_model=candidate,
                    request_mode=mode,
                    max_input_tokens=max_input_tokens,
                    applied_input_budget=input_budget,
                )
        return None

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
        request_mode: ModelRequestMode = ModelRequestMode.JSON_ONLY,
        diagnostic_mode: bool = False,
        max_output_chars: int | None = None,
        response_model: type[BaseModel] = ActionEnvelope,
        compact_variant: PromptVariant | None = None,
        context_chars: int = 0,
        active_problem_id: str | None = None,
        candidate_evidence_id_count: int = 0,
        resolved_evidence_count: int = 0,
        unresolved_evidence_ids: list[str] | None = None,
        new_signal_count: int = 0,
        included_signal_count: int = 0,
        excluded_signal_count: int = 0,
        model_max_input_tokens: int | None = None,
        applied_input_budget: int | None = None,
    ) -> ModelCallResult:
        max_output_chars = max_output_chars or int(os.getenv("MAX_TOTAL_OUTPUT_CHARS", "60000"))
        model_max_input_tokens = model_max_input_tokens or DEFAULT_MODEL_MAX_INPUT_TOKENS
        applied_input_budget = applied_input_budget or model_input_budget(model_max_input_tokens)
        standard_variant = PromptVariant(
            messages=messages,
            response_model=response_model,
            context_chars=context_chars,
            active_problem_id=active_problem_id,
            candidate_evidence_id_count=candidate_evidence_id_count,
            resolved_evidence_count=resolved_evidence_count,
            unresolved_evidence_ids=unresolved_evidence_ids or [],
            new_signal_count=new_signal_count,
            included_signal_count=included_signal_count,
            excluded_signal_count=excluded_signal_count,
        )
        active_variant = standard_variant
        using_compact = False
        diagnostic = ModelInferenceDiagnostic(
            selected_model=model,
            request_mode=request_mode,
            selected_model_max_input_tokens=model_max_input_tokens,
            applied_input_budget=applied_input_budget,
        )
        current_mode = request_mode
        calls = 0
        call_limit = 1 if diagnostic_mode else min(2, self.limiter.max_run_calls)
        last_error = ModelPipelineError(
            FailureStage.REQUEST_BUILD,
            "model request was not completed",
        )
        original_action_type: ActionType | None = None
        while calls < call_limit:
            original_action_type = None
            diagnostic.request_mode = current_mode
            diagnostic.http_status = None
            diagnostic.choices_count = None
            diagnostic.message_content_type = None
            diagnostic.response_char_count = 0
            diagnostic.finish_reason = None
            diagnostic.failure_stage = None
            try:
                base_payload: dict[str, Any] = {
                    "model": model,
                    "messages": active_variant.messages,
                    "temperature": 0,
                    "max_tokens": 500 if diagnostic_mode else 6000,
                    "stream": False,
                }
                payload = self._build_chat_payload(
                    base_payload,
                    current_mode,
                    diagnostic_mode=diagnostic_mode,
                    response_model=active_variant.response_model,
                )
            except (TypeError, ValueError):
                last_error = ModelPipelineError(
                    FailureStage.REQUEST_BUILD,
                    "model request payload could not be built",
                )
                self.limiter.record_failure()
                break
            schema_text = self._schema_text(active_variant.response_model)
            self._update_request_diagnostic(
                diagnostic,
                payload,
                schema_text=schema_text,
                schema_in_messages=current_mode == ModelRequestMode.JSON_ONLY,
                variant=active_variant,
            )
            if diagnostic.estimated_input_tokens > applied_input_budget:
                if compact_variant is not None and not using_compact:
                    active_variant = compact_variant
                    using_compact = True
                    continue
                last_error = ModelPipelineError(
                    FailureStage.REQUEST_BUILD,
                    "model request exceeded the applied input budget before HTTP transport",
                    code=ActionRejectionCode.INPUT_BUDGET_EXCEEDED,
                )
                self.limiter.record_failure()
                break
            fingerprint = request_fingerprint(
                {"kind": "chat", "payload": payload, "attempt": calls + 1}
            )
            reservation_id: str | None = None
            request_id: str | None = None
            try:
                reservation_id = self.limiter.reserve("chat", fingerprint)
                calls += 1
                response = self.client.post(CHAT_URL, json=payload)
            except UsageLimitReached:
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    "model usage limit or duplicate request protection stopped the call",
                )
                self.limiter.record_failure()
                break
            except httpx.HTTPError:
                if reservation_id is not None:
                    self.limiter.complete_request(
                        reservation_id, failed_after_request=True
                    )
                    reservation_id = None
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    "GitHub Models HTTP request failed",
                    retryable=True,
                )
                self.limiter.record_failure()
                if calls < call_limit:
                    diagnostic.retry_attempted = True
                    self.sleep(2 ** max(calls - 1, 0))
                    continue
                break
            except Exception:
                if reservation_id is not None:
                    self.limiter.complete_request(
                        reservation_id, failed_after_request=True
                    )
                raise
            else:
                if reservation_id is not None:
                    request_id = self.limiter.complete_request(
                        reservation_id, failed_after_request=False
                    )
                    reservation_id = None
            diagnostic.http_status = response.status_code
            if response.status_code == 413:
                if request_id is not None:
                    self.limiter.mark_http_failed(request_id)
                self.limiter.record_failure()
                if compact_variant is not None and not using_compact and calls < call_limit:
                    diagnostic.compact_retry_attempted = True
                    diagnostic.retry_attempted = True
                    active_variant = compact_variant
                    using_compact = True
                    continue
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    "request_too_large",
                    code=ActionRejectionCode.REQUEST_TOO_LARGE,
                )
                break
            if response.status_code in {400, 422} and current_mode == ModelRequestMode.JSON_SCHEMA:
                if request_id is not None:
                    self.limiter.mark_http_failed(request_id)
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    "json_schema request was not supported by the selected model",
                    fallback_eligible=True,
                )
                self.limiter.record_failure()
                if calls < call_limit:
                    diagnostic.fallback_attempted = True
                    current_mode = ModelRequestMode.JSON_ONLY
                    continue
                break
            if response.status_code == 429 or response.status_code >= 500:
                if request_id is not None:
                    self.limiter.mark_http_failed(request_id)
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    f"GitHub Models returned HTTP {response.status_code}",
                    retryable=True,
                )
                self.limiter.record_failure()
                if calls < call_limit:
                    diagnostic.retry_attempted = True
                    self.sleep(2 ** max(calls - 1, 0))
                    continue
                break
            if not 200 <= response.status_code < 300:
                if request_id is not None:
                    self.limiter.mark_http_failed(request_id)
                last_error = ModelPipelineError(
                    FailureStage.HTTP_REQUEST,
                    f"GitHub Models returned HTTP {response.status_code}",
                )
                self.limiter.record_failure()
                break
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                if request_id is not None:
                    self.limiter.mark_response_validation_failed(request_id)
                last_error = ModelPipelineError(
                    FailureStage.RESPONSE_DECODE,
                    "GitHub Models returned a non-JSON response body",
                    retryable=True,
                    fallback_eligible=True,
                )
                self.limiter.record_failure()
                if self._continue_after_failure(
                    last_error, diagnostic, current_mode, calls, call_limit
                ):
                    current_mode = ModelRequestMode.JSON_ONLY
                    continue
                break
            try:
                action, original_action_type = self._decode_action_response(
                    data,
                    diagnostic,
                    max_output_chars=max_output_chars,
                    response_model=active_variant.response_model,
                )
            except ModelPipelineError as exc:
                if request_id is not None:
                    self.limiter.mark_response_validation_failed(request_id)
                last_error = exc
                original_action_type = exc.original_action_type or original_action_type
                self.limiter.record_failure()
                if exc.stage == FailureStage.SCHEMA_VALIDATION:
                    diagnostic.pydantic_validation_error_paths = exc.validation_paths
                    diagnostic.pydantic_validation_errors = exc.validation_errors
                    diagnostic.pydantic_validation_error_count = len(exc.validation_errors)
                    if calls < call_limit:
                        diagnostic.validation_correction_attempted = True
                        diagnostic.retry_attempted = True
                        if current_mode == ModelRequestMode.JSON_SCHEMA:
                            diagnostic.fallback_attempted = True
                            current_mode = ModelRequestMode.JSON_ONLY
                        correction_base = compact_variant or active_variant
                        if compact_variant is not None:
                            using_compact = True
                        active_variant = self._validation_correction_variant(
                            correction_base, exc
                        )
                        continue
                    break
                if self._continue_after_failure(
                    exc, diagnostic, current_mode, calls, call_limit
                ):
                    if current_mode == ModelRequestMode.JSON_SCHEMA:
                        current_mode = ModelRequestMode.JSON_ONLY
                    else:
                        diagnostic.retry_attempted = True
                    continue
                break
            diagnostic.failure_stage = None
            self._update_usage_diagnostic(diagnostic)
            return ModelCallResult(
                action=action,
                original_action_type=original_action_type,
                diagnostic=diagnostic,
            )
        diagnostic.failure_stage = last_error.stage
        if last_error.validation_errors:
            diagnostic.pydantic_validation_error_paths = last_error.validation_paths
            diagnostic.pydantic_validation_errors = last_error.validation_errors
            diagnostic.pydantic_validation_error_count = len(last_error.validation_errors)
        self.limiter.release_run_reservations()
        self._update_usage_diagnostic(diagnostic)
        return ModelCallResult(
            action=safe_no_op(last_error.reason),
            original_action_type=original_action_type,
            diagnostic=diagnostic,
            rejection_code=last_error.code,
            rejection_reason=last_error.reason,
        )

    @staticmethod
    def _schema_text(response_model: type[BaseModel]) -> str:
        return json.dumps(
            response_model.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _update_request_diagnostic(
        diagnostic: ModelInferenceDiagnostic,
        payload: dict[str, Any],
        *,
        schema_text: str,
        schema_in_messages: bool,
        variant: PromptVariant,
    ) -> None:
        messages = payload.get("messages", [])
        diagnostic.request_body_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        diagnostic.system_prompt_chars = sum(
            len(item.get("content", ""))
            for item in messages
            if item.get("role") == "system"
        )
        diagnostic.user_prompt_chars = sum(
            len(item.get("content", ""))
            for item in messages
            if item.get("role") == "user"
        )
        diagnostic.schema_chars = len(schema_text)
        diagnostic.context_chars = variant.context_chars
        diagnostic.estimated_input_tokens = estimate_input_tokens(
            messages,
            schema_text,
            schema_in_messages=schema_in_messages,
        )
        diagnostic.active_problem_id = variant.active_problem_id
        diagnostic.candidate_evidence_id_count = variant.candidate_evidence_id_count
        diagnostic.resolved_evidence_count = variant.resolved_evidence_count
        diagnostic.unresolved_evidence_ids = variant.unresolved_evidence_ids or []
        diagnostic.new_signal_count = variant.new_signal_count
        diagnostic.included_signal_count = variant.included_signal_count
        diagnostic.excluded_signal_count = variant.excluded_signal_count

    def _update_usage_diagnostic(self, diagnostic: ModelInferenceDiagnostic) -> None:
        usage = self.limiter.run_usage()
        diagnostic.completed_inference_calls = usage["completed_inference_calls"]
        diagnostic.reserved_inference_calls = usage["reserved_inference_calls"]
        diagnostic.failed_after_request_calls = usage["failed_after_request_calls"]
        diagnostic.http_failed_calls = usage["http_failed_calls"]
        diagnostic.response_validation_failed_calls = usage[
            "response_validation_failed_calls"
        ]

    @staticmethod
    def _validation_correction_variant(
        variant: PromptVariant,
        error: ModelPipelineError,
    ) -> PromptVariant:
        safe_errors = [
            {
                "path": item.path,
                "type": item.error_type,
                "missing_field": item.missing_field,
                "extra_field": item.extra_field,
                "expected_type": item.expected_type,
            }
            for item in error.validation_errors
        ]
        correction = (
            "Your previous JSON failed schema validation. Do not repeat or quote the "
            "previous response. Return one corrected JSON object only. Allowed DISCOVERY "
            "action_type values are collect_signals, create_problem_candidate, "
            "validate_evidence, write_report, and no_op. Validation diagnostics: "
            + json.dumps(safe_errors, ensure_ascii=False, separators=(",", ":"))
            + ". Correct create_problem_candidate example: "
            + json.dumps(
                DISCOVERY_CORRECTION_EXAMPLE,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return PromptVariant(
            messages=[*variant.messages, {"role": "system", "content": correction}],
            response_model=variant.response_model,
            context_chars=variant.context_chars,
            active_problem_id=variant.active_problem_id,
            candidate_evidence_id_count=variant.candidate_evidence_id_count,
            resolved_evidence_count=variant.resolved_evidence_count,
            unresolved_evidence_ids=variant.unresolved_evidence_ids or [],
            new_signal_count=variant.new_signal_count,
            included_signal_count=variant.included_signal_count,
            excluded_signal_count=variant.excluded_signal_count,
        )

    @staticmethod
    def _build_chat_payload(
        base_payload: dict[str, Any],
        request_mode: ModelRequestMode,
        *,
        diagnostic_mode: bool,
        response_model: type[BaseModel] = ActionEnvelope,
    ) -> dict[str, Any]:
        payload = dict(base_payload)
        messages = list(base_payload["messages"])
        if diagnostic_mode:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Pipeline diagnostic mode. Return exactly this JSON object and nothing "
                        f"else: {json.dumps(DIAGNOSTIC_ACTION, separators=(',', ':'))}"
                    ),
                }
            )
        if request_mode == ModelRequestMode.JSON_SCHEMA:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "zerofounder_action",
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object and no Markdown. Validate it against "
                        "this JSON Schema: "
                        + json.dumps(
                            response_model.model_json_schema(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                }
            )
        payload["messages"] = messages
        return payload

    @staticmethod
    def _continue_after_failure(
        error: ModelPipelineError,
        diagnostic: ModelInferenceDiagnostic,
        request_mode: ModelRequestMode,
        calls: int,
        call_limit: int,
    ) -> bool:
        if calls >= call_limit:
            return False
        if request_mode == ModelRequestMode.JSON_SCHEMA and error.fallback_eligible:
            diagnostic.fallback_attempted = True
            return True
        if error.retryable:
            diagnostic.retry_attempted = True
            return True
        return False

    @staticmethod
    def _decode_action_response(
        data: object,
        diagnostic: ModelInferenceDiagnostic,
        *,
        max_output_chars: int,
        response_model: type[BaseModel] = ActionEnvelope,
    ) -> tuple[ActionEnvelope, ActionType | None]:
        if not isinstance(data, dict):
            raise ModelPipelineError(
                FailureStage.RESPONSE_DECODE,
                "GitHub Models response JSON was not an object",
                retryable=True,
                fallback_eligible=True,
            )
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise ModelPipelineError(
                FailureStage.CHOICE_EXTRACTION,
                "GitHub Models response did not contain a choices array",
                retryable=True,
                fallback_eligible=True,
            )
        diagnostic.choices_count = len(choices)
        if not choices or not isinstance(choices[0], dict):
            raise ModelPipelineError(
                FailureStage.CHOICE_EXTRACTION,
                "GitHub Models response contained no usable choice",
                retryable=True,
                fallback_eligible=True,
            )
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        diagnostic.finish_reason = str(finish_reason)[:100] if finish_reason is not None else None
        if finish_reason == "length":
            raise ModelPipelineError(
                FailureStage.FINISH_REASON_CHECK,
                "model response was truncated by the output token limit",
                code=ActionRejectionCode.TRUNCATED_MODEL_RESPONSE,
            )
        if finish_reason == "content_filter":
            raise ModelPipelineError(
                FailureStage.FINISH_REASON_CHECK,
                "model response was blocked by a content filter",
                code=ActionRejectionCode.MODEL_CONTENT_FILTERED,
            )
        if finish_reason not in {None, "stop"}:
            raise ModelPipelineError(
                FailureStage.FINISH_REASON_CHECK,
                "model response used an unsupported finish reason",
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            diagnostic.message_content_type = MessageContentType.MISSING
            raise ModelPipelineError(
                FailureStage.CONTENT_EXTRACTION,
                "model choice did not contain a message object",
                retryable=True,
                fallback_eligible=True,
            )
        diagnostic.message_content_type = _content_type(message)
        refusal = message.get("refusal")
        if refusal is not None and refusal is not False and refusal != "":
            raise ModelPipelineError(
                FailureStage.CONTENT_EXTRACTION,
                "model refused to provide the requested structured action",
                code=ActionRejectionCode.MODEL_REFUSAL,
            )
        content = _extract_text_content(message)
        diagnostic.response_char_count = len(content)
        if not content:
            raise ModelPipelineError(
                FailureStage.CONTENT_EXTRACTION,
                "model message contained no text content",
                retryable=True,
                fallback_eligible=True,
            )
        if len(content) > max_output_chars:
            raise ModelPipelineError(
                FailureStage.CONTENT_EXTRACTION,
                "model text content exceeded the configured character limit",
            )
        parsed, original_action_type = _extract_json_object(content)
        try:
            validated = response_model.model_validate(parsed)
            if isinstance(validated, ActionEnvelope):
                action = validated
            elif hasattr(validated, "to_action_envelope"):
                action = validated.to_action_envelope()
            else:
                action = ActionEnvelope.model_validate(
                    validated.model_dump(mode="json", by_alias=True)
                )
        except ValidationError as exc:
            validation_errors = _validation_error_details(exc)
            raise ModelPipelineError(
                FailureStage.SCHEMA_VALIDATION,
                "model JSON did not satisfy the action schema",
                retryable=True,
                fallback_eligible=True,
                validation_paths=[item.path for item in validation_errors],
                validation_errors=validation_errors,
                original_action_type=original_action_type,
            ) from exc
        return action, original_action_type

    def embeddings(self, *, model: str, texts: list[str]) -> list[list[float]] | None:
        if not texts or len(texts) > 64:
            return None
        payload = {
            "model": model,
            "input": [text[:8000] for text in texts],
            "encoding_format": "float",
        }
        fingerprint = request_fingerprint({"kind": "embedding", "payload": payload})
        reservation_id: str | None = None
        try:
            reservation_id = self.limiter.reserve("embedding", fingerprint)
            response = self.client.post(EMBEDDINGS_URL, json=payload)
            self.limiter.complete_request(
                reservation_id, failed_after_request=response.status_code >= 400
            )
            reservation_id = None
            response.raise_for_status()
            data = response.json().get("data", [])
            vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
            if len(vectors) != len(texts):
                return None
            return vectors
        except httpx.HTTPError:
            if reservation_id is not None:
                self.limiter.complete_request(reservation_id, failed_after_request=True)
                reservation_id = None
            self.limiter.record_failure()
            return None
        except (UsageLimitReached, KeyError, TypeError, ValueError):
            self.limiter.record_failure()
            return None
        finally:
            if reservation_id is not None:
                self.limiter.release(reservation_id)
