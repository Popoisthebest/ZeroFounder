from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agents.safety import load_evidence_index
from agents.schemas import (
    ActionEnvelope,
    ActionType,
    FileChange,
    ProblemCandidate,
    ProblemEvidenceReference,
)

PAIN_TERMS = {"frustrating", "manual", "tedious", "broken", "unable", "불편", "수동"}
BUILD_RISKS = {"backend", "server", "realtime", "payment", "account", "personal data"}
STATIC_FIT = {"browser", "static", "checklist", "template", "visual", "interface"}


def _source_reliability(root: Path) -> dict[str, float]:
    path = root / "signals/sources.json"
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, float] = {}
    for pack in config.get("packs", []):
        if not isinstance(pack, dict):
            continue
        for source in pack.get("sources", []):
            if isinstance(source, dict) and isinstance(source.get("source_type"), str):
                result[source["source_type"]] = float(source.get("reliability", 0.5))
    return result


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _evidence_quality(record: dict, reliability: dict[str, float]) -> float:
    source_type = str(record.get("source_type") or "unknown")
    source_score = reliability.get(source_type, 0.5)
    observed = _parse_time(record.get("published_at")) or _parse_time(record.get("collected_at"))
    age_days = (
        max(0.0, (datetime.now(UTC) - observed).total_seconds() / 86_400)
        if observed
        else 180
    )
    recency = max(0.0, 1.0 - age_days / 180)
    summary = str(record.get("summary") or record.get("title") or "")
    specificity = min(1.0, 0.3 + min(len(summary), 500) / 800)
    return 0.45 * source_score + 0.3 * recency + 0.25 * specificity


def materialize_problem_candidate(action: ActionEnvelope, root: Path) -> FileChange:
    if action.action_type != ActionType.CREATE_PROBLEM_CANDIDATE or not action.problem_candidate:
        raise ValueError("problem candidate action is required")
    if action.files:
        raise ValueError("model-provided file paths are forbidden for problem candidates")
    index = load_evidence_index(root)
    missing = [evidence_id for evidence_id in action.evidence_ids if evidence_id not in index]
    if missing:
        raise ValueError(f"unknown evidence ids: {', '.join(missing)}")

    records = [index[evidence_id] for evidence_id in action.evidence_ids]
    references = [
        ProblemEvidenceReference(
            evidence_id=evidence_id,
            source_type=str(record.get("source_type") or "unknown"),
            url=str(record["url"]),
            summary=str(record.get("summary") or record.get("title") or "Evidence")[:500],
        )
        for evidence_id, record in zip(action.evidence_ids, records, strict=True)
    ]
    unique_urls = {reference.url for reference in references}
    source_types = {reference.source_type for reference in references}
    reliability = _source_reliability(root)
    qualities = [_evidence_quality(record, reliability) for record in records]
    average_quality = sum(qualities) / len(qualities)

    proposal = action.problem_candidate
    problem_text = f"{proposal.description} {proposal.current_workaround}".lower()
    pain_hits = sum(term in problem_text for term in PAIN_TERMS)
    frequency_score = min(10, len(unique_urls) * 2 + len(source_types))
    severity_score = min(10, 3 + pain_hits + min(len(unique_urls), 3))
    buildability_score = max(
        0,
        min(
            10,
            7
            + sum(term in problem_text for term in STATIC_FIT)
            - 2 * sum(term in problem_text for term in BUILD_RISKS),
        ),
    )
    confidence = round(
        min(
            0.95,
            average_quality * 0.7
            + min(len(unique_urls), 5) * 0.04
            + min(len(source_types), 3) * 0.03,
        ),
        3,
    )
    stored = ProblemCandidate(
        problem_id=proposal.problem_id,
        title=proposal.title,
        target_users=proposal.target_users,
        description=proposal.description,
        current_workaround=proposal.current_workaround,
        evidence_ids=action.evidence_ids,
        evidence=references,
        frequency_score=frequency_score,
        severity_score=severity_score,
        buildability_score=buildability_score,
        confidence=confidence,
    )
    path = f"research/problems/{stored.problem_id}.json"
    return FileChange(path=path, content=stored.model_dump_json(indent=2) + "\n")
