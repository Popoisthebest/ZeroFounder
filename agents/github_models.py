from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    ActionType,
    AgentRole,
    FailureStage,
    IdeaCandidateProposal,
    MessageContentType,
    ModelCallResult,
    ModelCandidateDiagnostic,
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
DEFAULT_MAX_MODEL_OUTPUT_TOKENS = 6000
CONSERVATIVE_FREE_INPUT_TOKENS = 6000
DEFAULT_MAX_INPUT_CHARS = 24_000
DEFAULT_TEXT_MODEL_CANDIDATES = (
    "cohere/cohere-command-a",
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1",
)
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
class CreateIdeaCandidatesCorrectionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    action_type: Literal[ActionType.CREATE_IDEA_CANDIDATES]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    idea_candidates: list[IdeaCandidateProposal] = Field(min_length=2, max_length=8)

    def to_action_envelope(self) -> ActionEnvelope:
        return ActionEnvelope.model_validate(self.model_dump(mode="json"))


class CreateProblemCandidateActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    action_type: Literal[ActionType.CREATE_PROBLEM_CANDIDATE]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    problem_candidate: Any
    state_transition: Any | None = None
    files: list[Any] = Field(default_factory=list, max_length=0)

    def to_action_envelope(self) -> ActionEnvelope:
        return ActionEnvelope.model_validate(self.model_dump(mode="json"))


class ValidateEvidenceActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    action_type: Literal[ActionType.VALIDATE_EVIDENCE]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    state_transition: Any | None = None
    files: list[Any] = Field(default_factory=list, max_length=0)

    def to_action_envelope(self) -> ActionEnvelope:
        return ActionEnvelope.model_validate(self.model_dump(mode="json"))


class EvaluateIdeasActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    action_type: Literal[ActionType.EVALUATE_IDEAS]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    idea_candidate_ids: list[str] | None = Field(default=None, max_length=20)
    idea_evaluations: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    state_transition: Any | None = None
    files: list[Any] = Field(default_factory=list, max_length=50)

    def to_action_envelope(self) -> ActionEnvelope:
        return ActionEnvelope.model_validate(self.model_dump(mode="json"))


class WriteReportActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    action_type: Literal[ActionType.WRITE_REPORT]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel
    requires_approval: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    files: list[Any] = Field(default_factory=list, min_length=1, max_length=50)
    state_transition: Any | None = None

    def to_action_envelope(self) -> ActionEnvelope:
        return ActionEnvelope.model_validate(self.model_dump(mode="json"))


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
    problem_loaded: bool = False
    problem_evidence_count: int = 0
    existing_idea_candidate_count: int = 0
    idea_context_ready: bool = False
    included_signal_count: int = 0
    excluded_signal_count: int = 0
    allowed_evidence_ids: list[str] = field(default_factory=list)
    is_validation_correction: bool = False
    compacted_context: bool = False
    removed_context_sections: list[str] = field(default_factory=list)


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


def correction_token_reserve(applied_input_budget: int) -> int:
    if applied_input_budget < 1200:
        return max(0, applied_input_budget // 5)
    return min(1000, max(800, applied_input_budget // 6))


def configured_model_input_budget() -> int:
    try:
        value = int(os.getenv("MAX_MODEL_INPUT_TOKENS", str(DEFAULT_MAX_MODEL_INPUT_TOKENS)))
    except ValueError:
        return DEFAULT_MAX_MODEL_INPUT_TOKENS
    return max(1, value)


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
        failed_action_fragment: dict[str, Any] | None = None,
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
        self.failed_action_fragment = failed_action_fragment or {}
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


def _idea_validator_name(message: str) -> str | None:
    if "market figures or execution results" in message:
        return "IdeaCandidateProposal.reject_invented_external_claims"
    if "cannot contain URLs" in message:
        return "IdeaCandidateProposal.reject_invented_external_claims"
    if "idea_candidate idea_id values must be unique" in message:
        return "ActionEnvelope.enforce_action_shape"
    if "create_idea_candidates requires at least two" in message:
        return "ActionEnvelope.enforce_action_shape"
    return None


def _candidate_at(parsed: dict[str, Any] | None, index: int | None) -> dict[str, Any] | None:
    if parsed is None or index is None:
        return None
    candidates = parsed.get("idea_candidates")
    if not isinstance(candidates, list) or index >= len(candidates):
        return None
    candidate = candidates[index]
    return candidate if isinstance(candidate, dict) else None


def _validation_error_details(
    exc: ValidationError,
    parsed: dict[str, Any] | None = None,
) -> list[PydanticErrorDiagnostic]:
    details: list[PydanticErrorDiagnostic] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = error.get("loc", ())
        path = ".".join(str(item) for item in location) or "<root>"
        error_type = str(error.get("type") or "validation_error")[:100]
        message = str(error.get("msg") or "validation failed")[:300]
        leaf = str(location[-1])[:200] if location else None
        candidate_index = None
        if len(location) >= 2 and location[0] == "idea_candidates" and isinstance(
            location[1], int
        ):
            candidate_index = location[1]
        candidate = _candidate_at(parsed, candidate_index)
        idea_id = candidate.get("idea_id") if candidate else None
        idea_id = idea_id if isinstance(idea_id, str) else None
        failure_field_path = path
        if candidate_index is not None and len(location) == 2:
            failure_field_path = f"idea_candidates.{candidate_index}"
        details.append(
            PydanticErrorDiagnostic(
                path=path[:300],
                error_type=error_type,
                message=message,
                candidate_index=candidate_index,
                idea_id=idea_id[:128] if idea_id else None,
                validator_name=_idea_validator_name(message),
                failure_field_path=failure_field_path[:300],
                missing_field=leaf if error_type == "missing" else None,
                extra_field=leaf if error_type == "extra_forbidden" else None,
                expected_type=_expected_type(error_type),
            )
        )
    return details[:50]


def _short_value(value: Any, *, max_chars: int = 500) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)[:max_chars]
    if isinstance(value, list):
        return [_short_value(item, max_chars=max_chars // 2) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _short_value(item, max_chars=max_chars // 2)
            for key, item in list(value.items())[:20]
        }
    return value


def _latest_user_json(messages: list[dict[str, str]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        try:
            loaded = json.loads(message.get("content", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


def _failed_action_fragment(
    parsed: dict[str, Any],
    validation_errors: list[PydanticErrorDiagnostic],
) -> dict[str, Any]:
    failed_indexes = sorted(
        {
            item.candidate_index
            for item in validation_errors
            if item.candidate_index is not None
        }
    )
    candidates = parsed.get("idea_candidates")
    failed_candidates = []
    if isinstance(candidates, list):
        for index in failed_indexes[:8]:
            candidate = candidates[index] if index < len(candidates) else None
            if isinstance(candidate, dict):
                failed_candidates.append({"index": index, "json": _short_value(candidate)})
    evidence_ids = parsed.get("evidence_ids")
    return {
        "action_type": parsed.get("action_type"),
        "evidence_ids": evidence_ids if isinstance(evidence_ids, list) else [],
        "failed_candidates": failed_candidates,
    }


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


@dataclass(frozen=True)
class RequestBudgetProfile:
    action_type: ActionType
    response_model: type[BaseModel]
    system_instruction: str
    removed_sections: list[str]


class RequestBudgetManager:
    PROFILES = {
        ActionType.CREATE_PROBLEM_CANDIDATE: RequestBudgetProfile(
            action_type=ActionType.CREATE_PROBLEM_CANDIDATE,
            response_model=CreateProblemCandidateActionEnvelope,
            system_instruction=(
                "Return one create_problem_candidate JSON object only. Use stored signal "
                "summaries as evidence. Do not include unrelated lifecycle actions."
            ),
            removed_sections=[
                "full_action_schema",
                "unrelated_lifecycle_instructions",
                "repeated_safety_constraints",
                "full_signal_records",
                "verbose_problem_history",
            ],
        ),
        ActionType.VALIDATE_EVIDENCE: RequestBudgetProfile(
            action_type=ActionType.VALIDATE_EVIDENCE,
            response_model=ValidateEvidenceActionEnvelope,
            system_instruction=(
                "Return one validate_evidence JSON object only. Validate the active "
                "problem using the listed evidence IDs and summaries."
            ),
            removed_sections=[
                "full_action_schema",
                "unrelated_lifecycle_instructions",
                "repeated_safety_constraints",
                "full_signal_records",
                "verbose_validation_metadata",
            ],
        ),
        ActionType.CREATE_IDEA_CANDIDATES: RequestBudgetProfile(
            action_type=ActionType.CREATE_IDEA_CANDIDATES,
            response_model=CreateIdeaCandidatesCorrectionEnvelope,
            system_instruction=(
                "Return one create_idea_candidates JSON object only. Keep "
                "IDEA_EVALUATION state; do not include files or state_transition."
            ),
            removed_sections=[
                "full_action_schema",
                "unused_action_type_schema",
                "unrelated_lifecycle_instructions",
                "mission",
                "full_signal_records",
                "repeated_safety_constraints",
                "validation_metadata",
                "empty_existing_idea_candidates",
            ],
        ),
        ActionType.EVALUATE_IDEAS: RequestBudgetProfile(
            action_type=ActionType.EVALUATE_IDEAS,
            response_model=EvaluateIdeasActionEnvelope,
            system_instruction=(
                "Return one evaluate_ideas JSON object only. Compare the existing "
                "idea candidates and include the allowed state transition."
            ),
            removed_sections=[
                "full_action_schema",
                "unused_action_type_schema",
                "unrelated_lifecycle_instructions",
                "mission",
                "full_signal_records",
                "repeated_safety_constraints",
                "verbose_candidate_fields",
                "create_idea_candidates_instructions",
            ],
        ),
        ActionType.WRITE_REPORT: RequestBudgetProfile(
            action_type=ActionType.WRITE_REPORT,
            response_model=WriteReportActionEnvelope,
            system_instruction=(
                "Return one write_report JSON object only. Use the report target, "
                "required evidence, and allowed file path policy."
            ),
            removed_sections=[
                "full_action_schema",
                "unrelated_lifecycle_instructions",
                "full_signal_records",
                "repeated_safety_constraints",
                "verbose_repository_metadata",
            ],
        ),
    }

    def __init__(
        self,
        client: Any,
        *,
        diagnostic_mode: bool,
        applied_input_budget: int,
    ) -> None:
        self.client = client
        self.diagnostic_mode = diagnostic_mode
        self.applied_input_budget = applied_input_budget
        self.used_compact_variant = False
        self.used_profile_compaction = False
        self.used_correction_minimizer = False

    def prepare_payload(
        self,
        *,
        model: str,
        variant: PromptVariant,
        request_mode: ModelRequestMode,
        compact_variant: PromptVariant | None,
        diagnostic: ModelInferenceDiagnostic,
        calls: int,
    ) -> tuple[PromptVariant, ModelRequestMode, dict[str, Any]]:
        candidates: list[tuple[PromptVariant, ModelRequestMode]] = [
            (variant, request_mode)
        ]
        if not variant.is_validation_correction:
            if compact_variant is not None and not self.used_compact_variant:
                candidates.append((compact_variant, request_mode))
            profile_variant = self._profile_compact_variant(variant, level=1)
            if profile_variant is not None and not self.used_profile_compaction:
                candidates.append((profile_variant, ModelRequestMode.JSON_ONLY))
            tighter_variant = self._profile_compact_variant(variant, level=2)
            if tighter_variant is not None:
                candidates.append((tighter_variant, ModelRequestMode.JSON_ONLY))
        elif not self.used_correction_minimizer:
            candidates.append(
                (
                    self._minimal_validation_correction_variant(variant),
                    ModelRequestMode.JSON_ONLY,
                )
            )

        target = (
            diagnostic.correction_target_tokens
            if variant.is_validation_correction
            else (
                diagnostic.initial_target_tokens
                if calls == 0
                else self.applied_input_budget
            )
        )
        last_variant = variant
        for candidate, mode in candidates:
            payload, schema_text = self._build_payload(model, candidate, mode)
            self._update_diagnostic(diagnostic, payload, schema_text, mode, candidate)
            self._record_estimate(diagnostic, candidate)
            if diagnostic.estimated_input_tokens <= target:
                self._mark_used(candidate, compact_variant)
                return candidate, mode, payload
            last_variant = candidate
        self._mark_removed(diagnostic, last_variant)
        raise ModelPipelineError(
            FailureStage.REQUEST_BUILD,
            "model request exceeded the applied input budget before HTTP transport",
            code=ActionRejectionCode.INPUT_BUDGET_EXCEEDED,
        )

    def validation_correction_variant(
        self,
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
        action_type = error.original_action_type or _known_action_type_from_object(
            {"action_type": error.failed_action_fragment.get("action_type")}
        )
        if action_type == ActionType.CREATE_IDEA_CANDIDATES:
            allowed_evidence_ids = variant.allowed_evidence_ids or [
                str(item)
                for item in error.failed_action_fragment.get("evidence_ids", [])
                if isinstance(item, str)
            ]
            correction_payload = {
                "action_type": ActionType.CREATE_IDEA_CANDIDATES.value,
                "active_problem_id": variant.active_problem_id,
                "allowed_evidence_ids": allowed_evidence_ids[:20],
                "failed_candidates": error.failed_action_fragment.get(
                    "failed_candidates", []
                ),
                "validation_errors": safe_errors,
                "schema_requirements": self._schema_requirements(
                    ActionType.CREATE_IDEA_CANDIDATES
                ),
            }
            return self._correction_variant(
                variant,
                action_type=ActionType.CREATE_IDEA_CANDIDATES,
                payload=correction_payload,
                response_model=CreateIdeaCandidatesCorrectionEnvelope,
                allowed_evidence_ids=allowed_evidence_ids[:20],
            )

        target_action = (
            action_type if action_type in self.PROFILES else self._infer_action_type(variant)
        )
        if target_action in self.PROFILES:
            correction_payload = {
                "action_type": target_action.value,
                "active_problem_id": variant.active_problem_id,
                "allowed_evidence_ids": variant.allowed_evidence_ids[:20],
                "validation_errors": safe_errors,
                "schema_requirements": self._schema_requirements(target_action),
            }
            return self._correction_variant(
                variant,
                action_type=target_action,
                payload=correction_payload,
                response_model=self.PROFILES[target_action].response_model,
                allowed_evidence_ids=variant.allowed_evidence_ids,
            )

        correction = (
            "Your previous JSON failed schema validation. Do not repeat or quote the "
            "previous response. Return one corrected JSON object only. Validation "
            "diagnostics: "
            + json.dumps(safe_errors, ensure_ascii=False, separators=(",", ":"))
        )
        return self._copy_variant(
            variant,
            messages=[*variant.messages, {"role": "system", "content": correction}],
            is_validation_correction=True,
        )

    def _build_payload(
        self,
        model: str,
        variant: PromptVariant,
        mode: ModelRequestMode,
    ) -> tuple[dict[str, Any], str]:
        payload = self.client._build_chat_payload(
            {
                "model": model,
                "messages": variant.messages,
                "temperature": 0,
                "max_tokens": 500 if self.diagnostic_mode else 6000,
                "stream": False,
            },
            mode,
            diagnostic_mode=self.diagnostic_mode,
            response_model=variant.response_model,
        )
        return payload, self.client._schema_text(variant.response_model)

    def _update_diagnostic(
        self,
        diagnostic: ModelInferenceDiagnostic,
        payload: dict[str, Any],
        schema_text: str,
        mode: ModelRequestMode,
        variant: PromptVariant,
    ) -> None:
        self.client._update_request_diagnostic(
            diagnostic,
            payload,
            schema_text=schema_text,
            schema_in_messages=mode == ModelRequestMode.JSON_ONLY,
            variant=variant,
        )
        self._mark_removed(diagnostic, variant)

    @staticmethod
    def _record_estimate(
        diagnostic: ModelInferenceDiagnostic,
        variant: PromptVariant,
    ) -> None:
        if variant.is_validation_correction:
            diagnostic.correction_estimated_tokens = diagnostic.estimated_input_tokens
        else:
            diagnostic.initial_estimated_tokens = diagnostic.estimated_input_tokens

    def _mark_used(
        self,
        variant: PromptVariant,
        compact_variant: PromptVariant | None,
    ) -> None:
        if compact_variant is not None and variant is compact_variant:
            self.used_compact_variant = True
        if variant.compacted_context and variant is not compact_variant:
            self.used_profile_compaction = True
        if variant.is_validation_correction and variant.compacted_context:
            self.used_correction_minimizer = True

    @staticmethod
    def _mark_removed(
        diagnostic: ModelInferenceDiagnostic,
        variant: PromptVariant,
    ) -> None:
        removed = list(
            dict.fromkeys(
                [
                    *diagnostic.removed_context_sections,
                    *variant.removed_context_sections,
                ]
            )
        )[:20]
        diagnostic.removed_context_sections = removed
        diagnostic.compacted_context = bool(removed)

    def _infer_action_type(self, variant: PromptVariant) -> ActionType | None:
        for message in variant.messages:
            if message.get("role") != "system":
                continue
            try:
                loaded = json.loads(message.get("content", ""))
            except json.JSONDecodeError:
                continue
            if not isinstance(loaded, dict):
                continue
            policy = loaded.get("orchestration_policy")
            if not isinstance(policy, dict):
                continue
            preferred = policy.get("preferred_action_types")
            if isinstance(preferred, list):
                for item in preferred:
                    try:
                        action_type = ActionType(item)
                    except (TypeError, ValueError):
                        continue
                    if action_type in self.PROFILES:
                        return action_type
        payload = _latest_user_json(variant.messages)
        try:
            requested = ActionType(payload.get("required_action"))
        except (TypeError, ValueError):
            requested = None
        if requested in self.PROFILES:
            return requested
        stage = payload.get("lifecycle_stage")
        if stage == "IDEA_EVALUATION":
            return (
                ActionType.EVALUATE_IDEAS
                if variant.existing_idea_candidate_count > 0
                else ActionType.CREATE_IDEA_CANDIDATES
            )
        if stage == "EVIDENCE_VALIDATION":
            return ActionType.VALIDATE_EVIDENCE
        if stage == "DISCOVERY":
            return ActionType.CREATE_PROBLEM_CANDIDATE
        return None

    def _profile_compact_variant(
        self,
        variant: PromptVariant,
        *,
        level: int,
    ) -> PromptVariant | None:
        action_type = self._infer_action_type(variant)
        if action_type not in self.PROFILES:
            return None
        profile = self.PROFILES[action_type]
        payload = _latest_user_json(variant.messages)
        compact_payload = self._compact_payload(action_type, variant, payload, level=level)
        user_content = json.dumps(
            compact_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        removed = profile.removed_sections
        if level > 1:
            removed = [
                *removed,
                "verbose_candidate_fields",
                "long_problem_description",
                "long_evidence_summaries",
            ]
        return self._copy_variant(
            variant,
            messages=[
                {"role": "system", "content": profile.system_instruction},
                {"role": "user", "content": user_content},
            ],
            response_model=profile.response_model,
            context_chars=len(user_content),
            included_signal_count=self._included_count(action_type, compact_payload, variant),
            compacted_context=True,
            removed_context_sections=list(dict.fromkeys(removed)),
        )

    def _compact_payload(
        self,
        action_type: ActionType,
        variant: PromptVariant,
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        if action_type == ActionType.CREATE_PROBLEM_CANDIDATE:
            return self._compact_problem_candidate_payload(payload, level=level)
        if action_type == ActionType.VALIDATE_EVIDENCE:
            return self._compact_validate_evidence_payload(variant, payload, level=level)
        if action_type == ActionType.CREATE_IDEA_CANDIDATES:
            return self._compact_create_ideas_payload(variant, payload, level=level)
        if action_type == ActionType.EVALUATE_IDEAS:
            return self._compact_evaluate_ideas_payload(variant, payload, level=level)
        if action_type == ActionType.WRITE_REPORT:
            return self._compact_write_report_payload(variant, payload, level=level)
        return payload

    @staticmethod
    def _compact_problem_candidate_payload(
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        limit = 140 if level == 1 else 90
        signals = []
        for record in payload.get("representative_signals", [])[: (8 if level == 1 else 5)]:
            if not isinstance(record, dict):
                continue
            signals.append(
                {
                    "signal_id": record.get("signal_id"),
                    "title": _short_value(
                        record.get("title_or_summary") or record.get("title"),
                        max_chars=limit,
                    ),
                    "summary": _short_value(record.get("summary"), max_chars=limit * 2),
                }
            )
        return {
            "lifecycle_stage": "DISCOVERY",
            "required_action": ActionType.CREATE_PROBLEM_CANDIDATE.value,
            "representative_signals": signals,
            "signal_clusters": _short_value(payload.get("signal_clusters", []), max_chars=limit),
            "minimum_required_fields": [
                "problem_id",
                "title",
                "description",
                "target_users",
                "current_workaround",
                "evidence_ids",
            ],
        }

    @staticmethod
    def _compact_validate_evidence_payload(
        variant: PromptVariant,
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        limit = 220 if level == 1 else 120
        return {
            "lifecycle_stage": "EVIDENCE_VALIDATION",
            "required_action": ActionType.VALIDATE_EVIDENCE.value,
            "active_problem_id": variant.active_problem_id
            or payload.get("active_problem_id"),
            "active_problem": _compact_problem(
                payload.get("active_problem_candidate") or payload.get("active_problem"),
                variant,
                level=level,
            ),
            "candidate_evidence_ids": payload.get("candidate_evidence_ids")
            or variant.allowed_evidence_ids,
            "validated_evidence": _compact_evidence_records(
                payload.get("included_signal_records", []),
                variant.allowed_evidence_ids,
                limit=limit,
            ),
            "state_transition": {
                "from": "EVIDENCE_VALIDATION",
                "to": "IDEA_EVALUATION",
            },
        }

    @staticmethod
    def _compact_create_ideas_payload(
        variant: PromptVariant,
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        limit = 240 if level == 1 else 130
        return {
            "lifecycle_stage": "IDEA_EVALUATION",
            "required_action": ActionType.CREATE_IDEA_CANDIDATES.value,
            "active_problem_id": variant.active_problem_id,
            "active_problem": _compact_problem(
                payload.get("active_problem"),
                variant,
                level=level,
            ),
            "validated_evidence": _compact_evidence_records(
                payload.get("included_signal_records", []),
                variant.allowed_evidence_ids,
                limit=limit,
            ),
            "allowed_evidence_ids": variant.allowed_evidence_ids[:20],
            "output_rules": {
                "candidate_count": "2..8",
                "forbidden_fields": ["files", "state_transition"],
                "candidate_evidence_ids": "Use only allowed_evidence_ids.",
            },
        }

    @staticmethod
    def _compact_evaluate_ideas_payload(
        variant: PromptVariant,
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        limit = 220 if level == 1 else 120
        candidates = []
        for candidate in payload.get("existing_idea_candidates", [])[:8]:
            if not isinstance(candidate, dict):
                continue
            candidates.append(
                {
                    "idea_id": candidate.get("idea_id"),
                    "name": _short_value(candidate.get("name"), max_chars=80),
                    "summary": _short_value(candidate.get("summary"), max_chars=limit),
                    "value_proposition": _short_value(
                        candidate.get("value_proposition"), max_chars=limit
                    ),
                    "differentiation": _short_value(
                        candidate.get("differentiation"), max_chars=limit
                    ),
                    "feasibility": _short_value(candidate.get("feasibility"), max_chars=limit),
                    "revenue_model": _short_value(
                        candidate.get("revenue_model"), max_chars=limit
                    ),
                    "evidence_ids": candidate.get("evidence_ids", []),
                    "risks": _short_value(candidate.get("risks", []), max_chars=limit),
                }
            )
        return {
            "lifecycle_stage": "IDEA_EVALUATION",
            "required_action": ActionType.EVALUATE_IDEAS.value,
            "active_problem_id": variant.active_problem_id,
            "active_problem": _compact_problem(
                payload.get("active_problem"),
                variant,
                level=level,
            ),
            "validated_evidence": _compact_evidence_records(
                payload.get("included_signal_records", []),
                variant.allowed_evidence_ids,
                limit=limit,
            ),
            "existing_idea_candidates": candidates,
            "evaluation_criteria": [
                "evidence fit",
                "clear user value",
                "differentiation",
                "feasibility",
                "risk",
            ],
            "state_transition": {
                "from": "IDEA_EVALUATION",
                "to": "DISTRIBUTION_CHECK",
            },
        }

    @staticmethod
    def _compact_write_report_payload(
        variant: PromptVariant,
        payload: dict[str, Any],
        *,
        level: int,
    ) -> dict[str, Any]:
        limit = 300 if level == 1 else 160
        return {
            "required_action": ActionType.WRITE_REPORT.value,
            "active_problem_id": variant.active_problem_id or payload.get("active_problem_id"),
            "report_target": _short_value(payload.get("report_target") or payload, max_chars=limit),
            "required_evidence": _compact_evidence_records(
                payload.get("included_signal_records", []),
                variant.allowed_evidence_ids,
                limit=limit,
            ),
        }

    @staticmethod
    def _schema_requirements(action_type: ActionType) -> dict[str, Any]:
        if action_type == ActionType.CREATE_IDEA_CANDIDATES:
            return {
                "action_type": action_type.value,
                "forbidden_fields": ["files", "state_transition"],
                "candidate_count": {"min": 2, "max": 8},
                "evidence_ids": "Use only allowed_evidence_ids.",
                "idea_id": "lowercase id matching ^[a-z0-9][a-z0-9._:-]{0,127}$",
                "string_rules": "No URLs, execution results, or invented metrics.",
            }
        return {
            "action_type": action_type.value,
            "schema_subset": [
                "role",
                "action_type",
                "title",
                "summary",
                "rationale",
                "risk_level",
                "requires_approval",
                "evidence_ids",
                "state_transition",
                "files",
                "idea_candidate_ids",
                "idea_evaluations",
                "problem_candidate",
            ],
        }

    def _correction_variant(
        self,
        variant: PromptVariant,
        *,
        action_type: ActionType,
        payload: dict[str, Any],
        response_model: type[BaseModel],
        allowed_evidence_ids: list[str],
    ) -> PromptVariant:
        user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._copy_variant(
            variant,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Return one corrected {action_type.value} JSON object only. "
                        "Do not quote the previous full response."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            response_model=response_model,
            context_chars=len(user_content),
            allowed_evidence_ids=allowed_evidence_ids,
            is_validation_correction=True,
            compacted_context=True,
            removed_context_sections=[
                "full_original_context",
                "failed_model_response_text",
                "full_action_schema",
                "included_signal_records",
            ],
        )

    def _minimal_validation_correction_variant(self, variant: PromptVariant) -> PromptVariant:
        user_payload = _latest_user_json(variant.messages)
        minimal_payload = {
            "action_type": user_payload.get("action_type"),
            "active_problem_id": user_payload.get("active_problem_id"),
            "allowed_evidence_ids": user_payload.get("allowed_evidence_ids", [])[:20],
            "validation_errors": user_payload.get("validation_errors", [])[:20],
            "schema_requirements": user_payload.get("schema_requirements", {}),
        }
        if "failed_candidates" in user_payload:
            minimal_payload["failed_candidates"] = [
                {
                    "index": item.get("index"),
                    "idea_id": (
                        item.get("json", {}).get("idea_id")
                        if isinstance(item.get("json"), dict)
                        else None
                    ),
                }
                for item in user_payload.get("failed_candidates", [])[:8]
                if isinstance(item, dict)
            ]
        return self._copy_variant(
            variant,
            messages=[
                {
                    "role": "system",
                    "content": "Return one corrected JSON object only.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        minimal_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            context_chars=0,
            is_validation_correction=True,
            compacted_context=True,
            removed_context_sections=[
                *variant.removed_context_sections,
                "failed_candidate_json",
            ],
        )

    @staticmethod
    def _included_count(
        action_type: ActionType,
        payload: dict[str, Any],
        variant: PromptVariant,
    ) -> int:
        if action_type == ActionType.EVALUATE_IDEAS:
            return len(payload.get("validated_evidence", []))
        if "validated_evidence" in payload:
            return len(payload["validated_evidence"])
        return variant.included_signal_count

    @staticmethod
    def _copy_variant(variant: PromptVariant, **updates: Any) -> PromptVariant:
        values = {
            "messages": variant.messages,
            "response_model": variant.response_model,
            "context_chars": variant.context_chars,
            "active_problem_id": variant.active_problem_id,
            "candidate_evidence_id_count": variant.candidate_evidence_id_count,
            "resolved_evidence_count": variant.resolved_evidence_count,
            "unresolved_evidence_ids": variant.unresolved_evidence_ids or [],
            "new_signal_count": variant.new_signal_count,
            "problem_loaded": variant.problem_loaded,
            "problem_evidence_count": variant.problem_evidence_count,
            "existing_idea_candidate_count": variant.existing_idea_candidate_count,
            "idea_context_ready": variant.idea_context_ready,
            "included_signal_count": variant.included_signal_count,
            "excluded_signal_count": variant.excluded_signal_count,
            "allowed_evidence_ids": variant.allowed_evidence_ids or [],
            "is_validation_correction": variant.is_validation_correction,
            "compacted_context": variant.compacted_context,
            "removed_context_sections": variant.removed_context_sections or [],
        }
        values.update(updates)
        return PromptVariant(**values)


def _compact_problem(
    problem: Any,
    variant: PromptVariant,
    *,
    level: int,
) -> dict[str, Any]:
    limit = 280 if level == 1 else 140
    if not isinstance(problem, dict):
        return {
            "problem_id": variant.active_problem_id,
            "title": None,
            "description": None,
            "target_users": [],
            "evidence_ids": variant.allowed_evidence_ids,
        }
    return {
        "problem_id": problem.get("problem_id") or variant.active_problem_id,
        "title": _short_value(problem.get("title"), max_chars=140),
        "description": _short_value(problem.get("description"), max_chars=limit),
        "target_users": _short_value(problem.get("target_users", []), max_chars=140),
        "evidence_ids": problem.get("evidence_ids") or variant.allowed_evidence_ids,
        "validation_result": _short_value(
            problem.get("validation_result", {}),
            max_chars=180 if level == 1 else 90,
        ),
    }


def _compact_evidence_records(
    records: Any,
    fallback_ids: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    evidence = []
    if isinstance(records, list):
        for record in records[:8]:
            if not isinstance(record, dict):
                continue
            evidence_id = record.get("signal_id") or record.get("evidence_id") or record.get("id")
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "title": _short_value(
                        record.get("title")
                        or record.get("title_or_summary")
                        or record.get("summary"),
                        max_chars=min(limit, 140),
                    ),
                    "summary": _short_value(record.get("summary"), max_chars=limit),
                }
            )
    if evidence:
        return evidence
    return [{"evidence_id": evidence_id, "summary": None} for evidence_id in fallback_ids[:8]]


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
    def _positive_int(value: object) -> int:
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit():
            parsed = int(value)
            return parsed if parsed > 0 else 0
        return 0

    @classmethod
    def _limit_value(cls, model: dict[str, Any], *keys: str) -> int:
        limits = model.get("limits")
        for key in keys:
            if isinstance(limits, dict):
                value = cls._positive_int(limits.get(key))
                if value:
                    return value
            value = cls._positive_int(model.get(key))
            if value:
                return value
        return 0

    @classmethod
    def _max_input_tokens(cls, model: dict[str, Any]) -> int:
        value = cls._limit_value(
            model,
            "max_input_tokens",
            "input_token_limit",
            "max_prompt_tokens",
            "prompt_token_limit",
        )
        if value:
            return value
        context_window = cls._context_window_tokens(model)
        if context_window:
            return max(1, context_window - cls._max_output_tokens(model))
        return DEFAULT_MODEL_MAX_INPUT_TOKENS

    @classmethod
    def _max_output_tokens(cls, model: dict[str, Any]) -> int:
        return cls._limit_value(
            model,
            "max_output_tokens",
            "output_token_limit",
            "max_completion_tokens",
            "completion_token_limit",
        ) or DEFAULT_MAX_MODEL_OUTPUT_TOKENS

    @classmethod
    def _context_window_tokens(cls, model: dict[str, Any]) -> int:
        value = cls._limit_value(
            model,
            "context_window",
            "context_window_tokens",
            "max_context_tokens",
            "context_length",
            "max_tokens",
        )
        if value:
            return value
        return cls._max_input_tokens_without_context(model) + cls._max_output_tokens(model)

    @classmethod
    def _max_input_tokens_without_context(cls, model: dict[str, Any]) -> int:
        return cls._limit_value(
            model,
            "max_input_tokens",
            "input_token_limit",
            "max_prompt_tokens",
            "prompt_token_limit",
        ) or DEFAULT_MODEL_MAX_INPUT_TOKENS

    @staticmethod
    def _configured_model() -> str | None:
        configured = (os.getenv("GITHUB_MODEL") or "").strip()
        return configured or None

    @staticmethod
    def _configured_fallback_models() -> list[str]:
        raw = os.getenv("GITHUB_FALLBACK_MODELS")
        if raw is None or not raw.strip():
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _unique_candidates(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for model_id, source in items:
            if model_id in seen:
                continue
            seen.add(model_id)
            unique.append((model_id, source))
        return unique

    @staticmethod
    def configured_input_budget() -> int:
        return configured_model_input_budget()

    def select_chat_model(
        self,
        catalog: list[dict[str, Any]],
        *,
        required_input_tokens: int = 0,
        required_output_tokens: int = DEFAULT_MAX_MODEL_OUTPUT_TOKENS,
    ) -> ModelSelection | None:
        return self.select_chat_model_with_diagnostics(
            catalog,
            required_input_tokens=required_input_tokens,
            required_output_tokens=required_output_tokens,
        )[0]

    def select_chat_model_with_diagnostics(
        self,
        catalog: list[dict[str, Any]],
        *,
        required_input_tokens: int = 0,
        required_output_tokens: int = DEFAULT_MAX_MODEL_OUTPUT_TOKENS,
    ) -> tuple[ModelSelection | None, ModelInferenceDiagnostic, ActionRejectionCode | None]:
        available = {model["id"]: model for model in catalog if self._is_text_model(model)}
        configured = self._configured_model()
        fallbacks = self._configured_fallback_models()
        default_candidates = list(DEFAULT_TEXT_MODEL_CANDIDATES)
        candidate_sources = self._unique_candidates(
            ([(configured, "configured_model")] if configured else [])
            + [(item, "configured_fallback") for item in fallbacks]
            + [(item, "default") for item in default_candidates]
            + [(item, "catalog") for item in sorted(available)]
        )
        required_input_tokens = max(0, required_input_tokens)
        diagnostic = ModelInferenceDiagnostic(
            configured_model=configured,
            configured_fallback_models=fallbacks,
            default_model_candidates=default_candidates,
            required_input_tokens=required_input_tokens,
            required_output_tokens=required_output_tokens,
        )
        if not candidate_sources:
            return None, diagnostic, ActionRejectionCode.NO_MODEL_CANDIDATES_CONFIGURED

        evaluations: list[ModelCandidateDiagnostic] = []
        for candidate, source in candidate_sources:
            model = available.get(candidate)
            if model is None:
                evaluations.append(
                    ModelCandidateDiagnostic(
                        candidate_model_id=candidate,
                        required_input_tokens=required_input_tokens,
                        required_output_tokens=required_output_tokens,
                        exclusion_reason="not_found_in_catalog",
                    )
                )
                continue
            max_input_tokens = self._max_input_tokens(model)
            context_window = self._context_window_tokens(model)
            exclusion_reason = None
            if required_input_tokens > max_input_tokens:
                exclusion_reason = "insufficient_max_input_tokens"
            elif required_input_tokens > model_input_budget(max_input_tokens):
                exclusion_reason = "insufficient_applied_input_budget"
            elif context_window < required_input_tokens + required_output_tokens:
                exclusion_reason = "insufficient_context_window"
            evaluations.append(
                ModelCandidateDiagnostic(
                    candidate_model_id=candidate,
                    candidate_max_input_tokens=max_input_tokens,
                    candidate_context_window=context_window,
                    required_input_tokens=required_input_tokens,
                    required_output_tokens=required_output_tokens,
                    exclusion_reason=exclusion_reason,
                )
            )
            if exclusion_reason is not None:
                continue
            mode = (
                ModelRequestMode.JSON_SCHEMA
                if self._supports_structured_output(model)
                else ModelRequestMode.JSON_ONLY
            )
            selection = ModelSelection(
                selected_model=candidate,
                request_mode=mode,
                max_input_tokens=max_input_tokens,
                applied_input_budget=model_input_budget(max_input_tokens),
            )
            return (
                selection,
                diagnostic.model_copy(
                    update={
                        "selected_model": candidate,
                        "request_mode": mode,
                        "selected_model_max_input_tokens": max_input_tokens,
                        "applied_input_budget": selection.applied_input_budget,
                        "evaluated_model_candidates": evaluations,
                        "selected_model_source": source,
                    }
                ),
                None,
            )
        return (
            None,
            diagnostic.model_copy(update={"evaluated_model_candidates": evaluations}),
            ActionRejectionCode.NO_COMPATIBLE_MODEL,
        )

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
        problem_loaded: bool = False,
        problem_evidence_count: int = 0,
        existing_idea_candidate_count: int = 0,
        idea_context_ready: bool = False,
        allowed_evidence_ids: list[str] | None = None,
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
            problem_loaded=problem_loaded,
            problem_evidence_count=problem_evidence_count,
            existing_idea_candidate_count=existing_idea_candidate_count,
            idea_context_ready=idea_context_ready,
            allowed_evidence_ids=allowed_evidence_ids or [],
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
            reserved_correction_tokens=(
                0 if diagnostic_mode else correction_token_reserve(applied_input_budget)
            ),
        )
        diagnostic.initial_target_tokens = max(
            1,
            applied_input_budget - diagnostic.reserved_correction_tokens,
        )
        diagnostic.correction_target_tokens = applied_input_budget
        budget_manager = RequestBudgetManager(
            self,
            diagnostic_mode=diagnostic_mode,
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
                active_variant, current_mode, payload = budget_manager.prepare_payload(
                    model=model,
                    variant=active_variant,
                    request_mode=current_mode,
                    compact_variant=compact_variant,
                    diagnostic=diagnostic,
                    calls=calls,
                )
            except (TypeError, ValueError):
                last_error = ModelPipelineError(
                    FailureStage.REQUEST_BUILD,
                    "model request payload could not be built",
                )
                self.limiter.record_failure()
                break
            except ModelPipelineError as exc:
                last_error = exc
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
                        active_variant = budget_manager.validation_correction_variant(
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
        diagnostic.problem_loaded = variant.problem_loaded
        diagnostic.problem_evidence_count = variant.problem_evidence_count
        diagnostic.existing_idea_candidate_count = variant.existing_idea_candidate_count
        diagnostic.idea_context_ready = variant.idea_context_ready
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
            validation_errors = _validation_error_details(exc, parsed)
            raise ModelPipelineError(
                FailureStage.SCHEMA_VALIDATION,
                "model JSON did not satisfy the action schema",
                retryable=True,
                fallback_eligible=True,
                validation_paths=[item.path for item in validation_errors],
                validation_errors=validation_errors,
                failed_action_fragment=_failed_action_fragment(parsed, validation_errors),
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
