from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.safety import load_evidence_index
from agents.schemas import (
    CompanyState,
    IdeaCandidate,
    LifecycleStage,
    MarketSignal,
    ProblemCandidate,
)
from agents.signal_context import build_discovery_signal_context


@dataclass(frozen=True)
class ContextBundle:
    content: str
    included_signal_count: int = 0
    excluded_signal_count: int = 0
    active_problem_id: str | None = None
    candidate_evidence_id_count: int = 0
    resolved_evidence_count: int = 0
    unresolved_evidence_ids: list[str] | None = None
    new_signal_count: int = 0
    problem_loaded: bool = False
    problem_evidence_count: int = 0
    existing_idea_candidate_count: int = 0
    idea_context_ready: bool = False
    allowed_evidence_ids: list[str] | None = None


def _read(path: Path, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(errors="replace")[-limit:]


def _json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _recent_json_records(directory: Path, *, limit: int) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*"), reverse=True):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix == ".jsonl":
                loaded = [json.loads(line) for line in path.read_text().splitlines() if line]
            else:
                value = json.loads(path.read_text())
                loaded = value if isinstance(value, list) else [value]
        except (OSError, json.JSONDecodeError):
            continue
        for item in reversed(loaded):
            if isinstance(item, dict):
                records.append(item)
            if len(records) >= limit:
                return list(reversed(records))
    return list(reversed(records))


def _fit_payload(payload: dict[str, Any], max_chars: int) -> str:
    result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(result) <= max_chars:
        return result
    # Fail closed with a valid minimal JSON object instead of slicing JSON mid-token.
    minimal = {
        "lifecycle_stage": payload.get("lifecycle_stage", "unknown"),
        "context_truncated": True,
    }
    return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))


def _ordered_unique(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _signal_payload(evidence_id: str, record: dict[str, Any], *, compact: bool) -> dict[str, Any]:
    try:
        signal = MarketSignal.model_validate(record)
        return {
            "signal_id": signal.signal_id,
            "title_or_summary": (signal.korean_title or signal.title)[: (90 if compact else 180)],
            "summary": (signal.korean_summary or signal.summary)[: (240 if compact else 500)],
            "source_type": signal.source_type,
            "url": signal.url,
            "published_at": (signal.published_at or signal.collected_at).isoformat(),
            "original_language": signal.original_language,
            "market_region": signal.market_region,
        }
    except ValueError:
        return {
            "signal_id": evidence_id,
            "title_or_summary": str(record.get("title") or evidence_id)[: (90 if compact else 180)],
            "summary": str(record.get("summary") or "")[: (240 if compact else 500)],
            "source_type": str(record.get("source_type") or "unknown"),
            "url": str(record.get("url") or record.get("source_url") or ""),
        }


def _active_problem_context(
    root: Path,
) -> tuple[str | None, ProblemCandidate | None, list[str]]:
    state_path = root / "company/state.json"
    if not state_path.exists():
        return None, None, []
    try:
        state = CompanyState.model_validate_json(state_path.read_text())
    except ValueError:
        return None, None, []
    active_problem_id = state.active_problem_id
    if not active_problem_id:
        return None, None, []
    problem_path = root / f"research/problems/{active_problem_id}.json"
    if not problem_path.exists():
        return active_problem_id, None, []
    try:
        problem = ProblemCandidate.model_validate_json(problem_path.read_text())
    except ValueError:
        return active_problem_id, None, []
    return active_problem_id, problem, list(problem.evidence_ids)


def _idea_candidates_for_problem(root: Path, active_problem_id: str | None) -> list[dict[str, Any]]:
    if not active_problem_id:
        return []
    records: list[dict[str, Any]] = []
    for item in _recent_json_records(root / "research/ideas", limit=100):
        if item.get("problem_id") == active_problem_id and isinstance(
            item.get("idea_candidates"), list
        ):
            records.extend(
                candidate
                for candidate in item["idea_candidates"]
                if isinstance(candidate, dict)
            )
            continue
        try:
            candidate = IdeaCandidate.model_validate(item)
        except ValueError:
            if item.get("problem_id") != active_problem_id:
                continue
            records.append(item)
            continue
        if candidate.problem_id == active_problem_id:
            records.append(candidate.model_dump(mode="json"))
    return records


def _discovery_context(root: Path, *, compact: bool, max_chars: int) -> ContextBundle:
    signals = build_discovery_signal_context(root, compact=compact)
    strategy = _json(root / "company/strategy.json")
    evidence_policy = strategy.get("evidence", {}) if isinstance(strategy, dict) else {}
    problem_limit = 2 if compact else 5
    problem_candidates = _recent_json_records(root / "research/problems", limit=problem_limit)
    payload: dict[str, Any] = {
        "lifecycle_stage": LifecycleStage.DISCOVERY.value,
        "mission": _read(root / "company/mission.md", 1200 if compact else 2000),
        "safety_constraints": _read(
            root / "company/constitution.md", 900 if compact else 1600
        ),
        "evidence_policy": evidence_policy,
        "representative_signals": signals.representative_signals,
        "signal_clusters": signals.signal_clusters,
        "problem_candidates": problem_candidates,
        "signal_stats": {
            "total_unique": signals.total_unique_signals,
            "included": signals.included_signal_count,
            "excluded": signals.excluded_signal_count,
        },
    }
    return ContextBundle(
        content=_fit_payload(payload, max_chars),
        included_signal_count=signals.included_signal_count,
        excluded_signal_count=signals.excluded_signal_count,
    )


def _evidence_validation_context(
    root: Path,
    *,
    compact: bool,
    max_chars: int,
    new_signal_ids: list[str],
) -> ContextBundle:
    active_problem_id, problem, candidate_evidence_ids = _active_problem_context(root)
    candidate_evidence_ids = _ordered_unique(candidate_evidence_ids)
    new_signal_ids = _ordered_unique(new_signal_ids)
    evidence_index = load_evidence_index(root)
    included_ids = _ordered_unique([*candidate_evidence_ids, *new_signal_ids])
    resolved_candidate_ids = [
        evidence_id for evidence_id in candidate_evidence_ids if evidence_id in evidence_index
    ]
    included_records = [
        _signal_payload(evidence_id, evidence_index[evidence_id], compact=compact)
        for evidence_id in included_ids
        if evidence_id in evidence_index
    ]
    unresolved_ids = [
        evidence_id for evidence_id in included_ids if evidence_id not in evidence_index
    ]
    payload: dict[str, Any] = {
        "lifecycle_stage": LifecycleStage.EVIDENCE_VALIDATION.value,
        "mission": _read(root / "company/mission.md", 1200 if compact else 2000),
        "safety_constraints": _read(
            root / "company/constitution.md", 900 if compact else 1600
        ),
        "active_problem_id": active_problem_id,
        "active_problem_candidate": problem.model_dump(mode="json") if problem else None,
        "candidate_evidence_ids": candidate_evidence_ids,
        "new_signal_ids": new_signal_ids,
        "included_signal_records": included_records,
        "unresolved_evidence_ids": unresolved_ids,
        "signal_stats": {
            "candidate_evidence_id_count": len(candidate_evidence_ids),
            "resolved_evidence_count": len(resolved_candidate_ids),
            "new_signal_count": len(new_signal_ids),
            "included": len(included_records),
            "unresolved": len(unresolved_ids),
        },
    }
    return ContextBundle(
        content=_fit_payload(payload, max_chars),
        included_signal_count=len(included_records),
        excluded_signal_count=0,
        active_problem_id=active_problem_id,
        candidate_evidence_id_count=len(candidate_evidence_ids),
        resolved_evidence_count=len(resolved_candidate_ids),
        unresolved_evidence_ids=unresolved_ids,
        new_signal_count=len(new_signal_ids),
    )


def _idea_evaluation_context(
    root: Path,
    *,
    compact: bool,
    max_chars: int,
) -> ContextBundle:
    active_problem_id, problem, candidate_evidence_ids = _active_problem_context(root)
    candidate_evidence_ids = _ordered_unique(candidate_evidence_ids)
    evidence_index = load_evidence_index(root)
    included_records = [
        _signal_payload(evidence_id, evidence_index[evidence_id], compact=compact)
        for evidence_id in candidate_evidence_ids
        if evidence_id in evidence_index
    ]
    unresolved_ids = [
        evidence_id for evidence_id in candidate_evidence_ids if evidence_id not in evidence_index
    ]
    idea_candidates = _idea_candidates_for_problem(root, active_problem_id)
    problem_loaded = problem is not None
    problem_evidence_count = len(candidate_evidence_ids)
    resolved_evidence_count = len(included_records)
    idea_context_ready = (
        bool(active_problem_id)
        and problem_loaded
        and problem_evidence_count > 0
        and resolved_evidence_count == problem_evidence_count
    )
    payload: dict[str, Any] = {
        "lifecycle_stage": LifecycleStage.IDEA_EVALUATION.value,
        "mission": _read(root / "company/mission.md", 1200 if compact else 2000),
        "safety_constraints": _read(
            root / "company/constitution.md", 900 if compact else 1600
        ),
        "active_problem_id": active_problem_id,
        "problem_loaded": problem_loaded,
        "active_problem": (
            {
                "problem_id": problem.problem_id,
                "title": problem.title,
                "description": problem.description,
                "target_users": problem.target_users,
                "evidence_ids": problem.evidence_ids,
                "validation_result": {
                    "lifecycle_stage": LifecycleStage.IDEA_EVALUATION.value,
                    "frequency_score": problem.frequency_score,
                    "severity_score": problem.severity_score,
                    "buildability_score": problem.buildability_score,
                    "confidence": problem.confidence,
                    "validated": True,
                },
            }
            if problem
            else None
        ),
        "existing_idea_candidates": idea_candidates[: (4 if compact else 12)],
        "included_signal_records": included_records,
        "unresolved_evidence_ids": unresolved_ids,
        "idea_stats": {
            "problem_evidence_count": problem_evidence_count,
            "resolved_evidence_count": resolved_evidence_count,
            "existing_idea_candidate_count": len(idea_candidates),
            "included_signal_count": len(included_records),
            "idea_context_ready": idea_context_ready,
        },
    }
    return ContextBundle(
        content=_fit_payload(payload, max_chars),
        included_signal_count=len(included_records),
        excluded_signal_count=0,
        active_problem_id=active_problem_id,
        candidate_evidence_id_count=problem_evidence_count,
        resolved_evidence_count=resolved_evidence_count,
        unresolved_evidence_ids=unresolved_ids,
        new_signal_count=0,
        problem_loaded=problem_loaded,
        problem_evidence_count=problem_evidence_count,
        existing_idea_candidate_count=len(idea_candidates),
        idea_context_ready=idea_context_ready,
        allowed_evidence_ids=candidate_evidence_ids,
    )


def _general_context(root: Path, *, compact: bool, max_chars: int) -> ContextBundle:
    fixed_names = [
        "mission",
        "state",
        "strategy",
        "venture",
        "metrics",
        "usage",
    ]
    paths = {
        "mission": root / "company/mission.md",
        "state": root / "company/state.json",
        "strategy": root / "company/strategy.json",
        "venture": root / "venture/venture.json",
        "metrics": root / "company/metrics.json",
        "usage": root / "company/usage.json",
    }
    payload: dict[str, Any] = {
        name: _read(paths[name], 2000 if compact else 5000) for name in fixed_names
    }
    if not compact:
        payload["tasks"] = _read(root / "company/task-board.json", 4000)
        payload["recent_decisions"] = _read(root / "company/decisions.jsonl", 6000)
        payload["founder_results_read_only"] = _read(root / "founder/results.json", 4000)
    return ContextBundle(content=_fit_payload(payload, max_chars))


def build_context_bundle(
    root: Path,
    *,
    lifecycle_stage: LifecycleStage | None = None,
    compact: bool = False,
    max_chars: int | None = None,
    new_signal_ids: list[str] | None = None,
) -> ContextBundle:
    if lifecycle_stage is None:
        state_path = root / "company/state.json"
        lifecycle_stage = (
            CompanyState.model_validate_json(state_path.read_text()).lifecycle_stage
            if state_path.exists()
            else LifecycleStage.DISCOVERY
        )
    configured_max = int(max_chars or (12_000 if compact else 24_000))
    if lifecycle_stage == LifecycleStage.DISCOVERY:
        return _discovery_context(root, compact=compact, max_chars=configured_max)
    if lifecycle_stage == LifecycleStage.EVIDENCE_VALIDATION:
        return _evidence_validation_context(
            root,
            compact=compact,
            max_chars=configured_max,
            new_signal_ids=new_signal_ids or [],
        )
    if lifecycle_stage == LifecycleStage.IDEA_EVALUATION:
        return _idea_evaluation_context(
            root,
            compact=compact,
            max_chars=configured_max,
        )
    return _general_context(root, compact=compact, max_chars=configured_max)


def build_context(
    root: Path,
    max_chars: int = 24_000,
    *,
    lifecycle_stage: LifecycleStage | None = None,
    compact: bool = False,
    new_signal_ids: list[str] | None = None,
) -> str:
    return build_context_bundle(
        root,
        lifecycle_stage=lifecycle_stage,
        compact=compact,
        max_chars=max_chars,
        new_signal_ids=new_signal_ids,
    ).content
