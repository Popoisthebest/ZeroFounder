from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.schemas import CompanyState, LifecycleStage
from agents.signal_context import build_discovery_signal_context


@dataclass(frozen=True)
class ContextBundle:
    content: str
    included_signal_count: int = 0
    excluded_signal_count: int = 0


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
    return _general_context(root, compact=compact, max_chars=configured_max)


def build_context(
    root: Path,
    max_chars: int = 24_000,
    *,
    lifecycle_stage: LifecycleStage | None = None,
    compact: bool = False,
) -> str:
    return build_context_bundle(
        root,
        lifecycle_stage=lifecycle_stage,
        compact=compact,
        max_chars=max_chars,
    ).content
