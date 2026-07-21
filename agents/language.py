from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

BOT_AUTHOR = re.compile(r"(?:\[bot\]|github-actions|zerofounder|agent)", re.I)
KOREAN = re.compile(r"[가-힣]")
JAPANESE = re.compile(r"[ぁ-ゟ゠-ヿ]")
CHINESE = re.compile(r"[一-鿿]")
ENGLISH_WORD = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")
MACHINE_VALUE = re.compile(
    r"^(?:[a-z][a-z0-9_.:-]{0,127}|[A-Z_]{2,}|[A-Za-z0-9._/-]+\.[A-Za-z0-9]+)$"
)

GLOBAL_MARKET_POLICY = {
    "research_scope": "global",
    "output_language_is_market_preference": False,
    "supported_evidence_languages": ["ko", "en", "ja", "zh", "other"],
    "region_neutral_evaluation": True,
}


def operating_language() -> str:
    configured = os.getenv("OPERATING_LANGUAGE", "ko").strip().lower()
    return configured if configured == "ko" else "ko"


def detect_language(value: str) -> str:
    if KOREAN.search(value):
        return "ko"
    if JAPANESE.search(value):
        return "ja"
    if CHINESE.search(value):
        return "zh"
    if value.strip() and value.isascii():
        return "en"
    return "und"


@dataclass(frozen=True)
class LanguageMismatch:
    path: str
    value: str
    detected_language: str


def _is_machine_value(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if "/" in stripped or stripped.startswith(("http://", "https://")):
        return True
    return bool(MACHINE_VALUE.fullmatch(stripped))


def _english_sentence_like(value: str) -> bool:
    stripped = value.strip()
    if KOREAN.search(stripped):
        return False
    words = ENGLISH_WORD.findall(stripped)
    return len(words) >= 2 and len(" ".join(words)) >= 10


def _collect_descriptive_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    descriptive_keys = {
        "title",
        "summary",
        "rationale",
        "description",
        "current_workaround",
        "name",
        "target_users",
        "proposed_solution",
        "value_proposition",
        "differentiation",
        "revenue_model",
        "feasibility",
        "risks",
        "evaluation_dimensions",
        "reason",
        "strengths",
        "weaknesses",
        "tradeoffs",
        "recommendation",
        "notes",
        "content",
        "standard_library_alternative",
        "security_risk",
        "bundle_or_maintenance_impact",
    }
    machine_keys = {
        "role",
        "action_type",
        "risk_level",
        "requires_approval",
        "evidence_ids",
        "idea_id",
        "problem_id",
        "proposal_id",
        "path",
        "operation",
        "from",
        "to",
        "status",
        "ecosystem",
        "package_name",
        "exact_version",
        "dependency_type",
        "requested_by_action",
        "license",
    }
    if isinstance(value, dict):
        output: list[tuple[str, str]] = []
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in machine_keys:
                continue
            if key_text in descriptive_keys:
                output.extend(_collect_descriptive_leaf(item, child_path))
            elif isinstance(item, dict | list):
                output.extend(_collect_descriptive_strings(item, child_path))
        return output
    if isinstance(value, list):
        output = []
        for index, item in enumerate(value):
            output.extend(_collect_descriptive_strings(item, f"{path}.{index}"))
        return output
    return []


def _collect_descriptive_leaf(value: Any, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        output: list[tuple[str, str]] = []
        for index, item in enumerate(value):
            output.extend(_collect_descriptive_leaf(item, f"{path}.{index}"))
        return output
    if isinstance(value, dict):
        return _collect_descriptive_strings(value, path)
    return []


def language_mismatches(
    payload: dict[str, Any],
    *,
    expected_language: str,
) -> list[LanguageMismatch]:
    if expected_language != "ko":
        return []
    candidates = [
        (path, text)
        for path, text in _collect_descriptive_strings(payload)
        if not _is_machine_value(text)
    ]
    if not candidates:
        return []
    mismatches = [
        LanguageMismatch(path=path, value=text[:200], detected_language="en")
        for path, text in candidates
        if _english_sentence_like(text)
    ]
    if not mismatches:
        return []
    korean_count = sum(1 for _, text in candidates if KOREAN.search(text))
    if len(mismatches) >= 2 or len(mismatches) > korean_count:
        return mismatches[:20]
    return []


def korean_output_contract() -> str:
    return (
        "출력 언어 계약: 모든 설명형 문자열(title, summary, rationale, 문제 설명, "
        "아이디어 평가의 reason/strengths/risks, 보고서 본문, 승인 요청 본문)은 "
        "자연스러운 한국어로 작성한다. idea_id, evidence_id, 파일 경로, enum, "
        "action_type, lifecycle_stage, 코드 값은 번역하지 않고 원문을 유지한다. "
        "영어 문장으로 설명하지 않는다."
    )


def localized_signal_fields(title: str, summary: str) -> dict[str, Any]:
    language = detect_language(f"{title} {summary}")
    is_korean = language == "ko"
    return {
        "original_language": language,
        "original_title": title,
        "original_summary": summary[:500] or None,
        "korean_title": title if is_korean else "외국어 시장 신호 — 한국어 요약 대기",
        "korean_summary": (
            summary
            if is_korean
            else "규칙 기반 수집 단계에서는 번역을 추정하지 않습니다. 원문과 출처를 보존했습니다."
        ),
        "market_region": ["global"],
        "translation_confidence": 1.0 if is_korean else 0.0,
    }


def choose_product_ui_language(target_languages: list[str]) -> str:
    normalized = [item.strip().lower() for item in target_languages if item.strip()]
    if not normalized:
        return "en"
    if len(normalized) > 1:
        return "en"
    return normalized[0]


def can_migrate_agent_generated(item: dict[str, Any]) -> bool:
    labels = item.get("labels", [])
    names = {
        str(label.get("name")) if isinstance(label, dict) else str(label)
        for label in labels
    }
    author = item.get("user") or item.get("author") or {}
    login = str(author.get("login") if isinstance(author, dict) else author)
    return "agent-generated" in names and bool(BOT_AUTHOR.search(login))


def migrated_korean_content(item: dict[str, Any], *, is_pull_request: bool) -> tuple[str, str]:
    if not can_migrate_agent_generated(item):
        raise ValueError("only agent-generated bot content can be migrated")
    number = int(item.get("number", 0))
    if is_pull_request:
        title = "chore(agent): 기존 에이전트 변경 설명을 한국어 형식으로 전환"
        body = f"""## 변경 목적

기존 자동 생성 PR #{number}의 창업자 검토 문구를 한국어 운영 형식으로 전환합니다.

## 생성된 산출물

- 기존 코드 변경은 수정하지 않습니다.

## 사용한 근거

- `agent-generated` 라벨과 봇 작성자 정보를 규칙 기반으로 확인했습니다.

## 상태 전환

- 없음

## 검증 결과

- 기존 PR의 branch와 commit은 유지합니다.

## 위험 및 제한 사항

- 사용자 작성 내용과 댓글은 변경하지 않습니다.

## 창업자 확인 사항

- 기존 diff와 Actions 결과를 직접 검토하세요.
"""
    else:
        title = "[에이전트 생성 알림] 기존 운영 Issue 한국어 형식 전환"
        body = f"""## 요청 내용

기존 자동 생성 Issue #{number}의 창업자 검토 문구를 한국어 형식으로 전환했습니다.

## 판단 근거

`agent-generated` 라벨과 봇 작성자 정보를 규칙 기반으로 확인했습니다.

## 대안

필요하면 Issue 이력에서 이전 자동 생성 내용을 확인할 수 있습니다.

## 위험 요소

사용자가 작성한 Issue와 댓글은 변경하지 않습니다.

## 승인 시 다음 단계

창업자가 내용을 검토한 뒤 필요한 명령을 직접 입력합니다.

## 사용 가능한 명령어

- `/approve`: 승인
- `/reject`: 거절
- `/revise`: 수정 요청
- `/pause`: 운영 일시정지
- `/resume`: 이전 단계로 복귀
- `/pivot`: 피벗 검토 요청
"""
    return title, body
