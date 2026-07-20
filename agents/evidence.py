from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime

from agents.schemas import Evidence, EvidenceClassification, MarketSignal
from agents.signal_collector import canonical_url, normalize_text

LEVEL_SCORE = {"low": 0.25, "medium": 0.6, "high": 0.9}
DIRECTNESS_SCORE = {"indirect": 0.2, "mixed": 0.6, "direct": 0.95}


def duplicate_cluster(signal: MarketSignal) -> str:
    normalized = re.sub(r"\W+", " ", f"{signal.title} {signal.summary}".lower()).strip()
    fingerprint = canonical_url(signal.url) + "\n" + normalized[:500]
    return f"cluster-{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"


def recency_score(signal: MarketSignal, max_age_days: int) -> float:
    if not signal.published_at:
        return 0.5
    published = signal.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    age = max(0, (datetime.now(UTC) - published).days)
    return round(max(0.0, 1.0 - age / max(max_age_days, 1)), 4)


def build_evidence(
    signal: MarketSignal,
    classification: EvidenceClassification,
    *,
    source_reliability: float,
    max_age_days: int,
    corroboration: float,
) -> Evidence:
    if classification.evidence_id != signal.signal_id:
        raise ValueError("classification must reference the stored signal id")
    recency = recency_score(signal, max_age_days)
    specificity = LEVEL_SCORE[classification.specificity]
    directness = DIRECTNESS_SCORE[classification.directness]
    quality = (
        specificity * 0.30
        + source_reliability * 0.25
        + recency * 0.20
        + directness * 0.15
        + max(0.0, min(corroboration, 1.0)) * 0.10
    )
    return Evidence(
        evidence_id=f"evidence-{signal.signal_id.removeprefix('signal-')}",
        signal_id=signal.signal_id,
        source_type=signal.source_type,
        url=signal.url,
        collected_at=signal.collected_at,
        published_at=signal.published_at,
        summary=normalize_text(signal.summary),
        duplicate_cluster=duplicate_cluster(signal),
        recency_score=recency,
        source_reliability=source_reliability,
        specificity_score=specificity,
        directness_score=directness,
        quality_score=round(quality, 4),
    )


def evidence_gate(
    evidence: list[Evidence],
    *,
    min_items: int,
    min_source_types: int,
    min_quality: float,
) -> tuple[bool, list[str]]:
    clusters = {item.duplicate_cluster for item in evidence}
    source_types = {item.source_type for item in evidence}
    external = [item for item in evidence if not item.source_type.startswith("github")]
    average = sum(item.quality_score for item in evidence) / len(evidence) if evidence else 0.0
    reasons: list[str] = []
    if len(clusters) < min_items:
        reasons.append("insufficient independent evidence")
    if len(source_types) < min_source_types:
        reasons.append("insufficient source types")
    if not external:
        reasons.append("at least one non-GitHub source is required")
    if average < min_quality or math.isnan(average):
        reasons.append("evidence quality is below threshold")
    return not reasons, reasons
