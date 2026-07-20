from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents.schemas import MarketSignal

PAIN_TERMS = {
    "annoying",
    "broken",
    "confusing",
    "difficult",
    "frustrating",
    "manual",
    "missing",
    "problem",
    "slow",
    "tedious",
    "unable",
    "workaround",
    "불편",
    "수동",
    "어려",
    "오류",
}
STOPWORDS = {
    "about",
    "after",
    "before",
    "could",
    "feature",
    "from",
    "have",
    "issue",
    "please",
    "that",
    "their",
    "there",
    "this",
    "using",
    "want",
    "with",
    "would",
}
WORD = re.compile(r"[\w-]{3,}", re.UNICODE)


@dataclass(frozen=True)
class RankedSignal:
    signal: MarketSignal
    quality: float
    recency: float
    specificity: float
    duplicate_cluster_id: str
    tokens: frozenset[str]


@dataclass(frozen=True)
class DiscoverySignalContext:
    representative_signals: list[dict[str, object]]
    signal_clusters: list[dict[str, object]]
    total_unique_signals: int
    included_signal_count: int
    excluded_signal_count: int


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in (item.lower() for item in WORD.findall(value))
        if token not in STOPWORDS
    )


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
            if not isinstance(source, dict) or not isinstance(source.get("source_type"), str):
                continue
            result[source["source_type"]] = float(source.get("reliability", 0.5))
    return result


def _load_signals(root: Path) -> list[MarketSignal]:
    records: list[MarketSignal] = []
    for path in sorted((root / "signals/raw").glob("*.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                records.append(MarketSignal.model_validate_json(line))
            except ValueError:
                continue
    return records


def _rank_signals(root: Path, *, max_age_days: int) -> list[RankedSignal]:
    reliability = _source_reliability(root)
    now = datetime.now(UTC)
    unique: dict[str, MarketSignal] = {}
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    for signal in _load_signals(root):
        normalized_title = " ".join(signal.title.lower().split())
        normalized_url = signal.url.rstrip("/").lower()
        duplicate_key = signal.content_hash or hashlib.sha256(
            f"{normalized_title}:{signal.url}".encode()
        ).hexdigest()
        if (
            duplicate_key in unique
            or normalized_title in seen_titles
            or normalized_url in seen_urls
        ):
            continue
        unique.setdefault(duplicate_key, signal)
        seen_titles.add(normalized_title)
        seen_urls.add(normalized_url)

    ranked: list[RankedSignal] = []
    for duplicate_key, signal in unique.items():
        observed_at = signal.published_at or signal.collected_at
        age_days = max(0.0, (now - observed_at).total_seconds() / 86_400)
        recency = max(0.0, 1.0 - age_days / max(max_age_days, 1))
        text = f"{signal.title} {signal.summary}"
        tokens = _tokens(text)
        pain_hits = sum(term in text.lower() for term in PAIN_TERMS)
        specificity = min(
            1.0,
            0.25
            + min(len(signal.summary), 600) / 1200
            + min(len(tokens), 20) / 80
            + min(pain_hits, 2) * 0.1,
        )
        directness = min(1.0, 0.45 + min(pain_hits, 3) * 0.18)
        source_score = reliability.get(signal.source_type, 0.5)
        quality = 0.4 * source_score + 0.25 * recency + 0.2 * specificity + 0.15 * directness
        ranked.append(
            RankedSignal(
                signal=signal,
                quality=round(quality, 4),
                recency=round(recency, 4),
                specificity=round(specificity, 4),
                duplicate_cluster_id=f"dup-{duplicate_key[:12]}",
                tokens=tokens,
            )
        )
    return sorted(
        ranked,
        key=lambda item: (
            item.quality,
            item.specificity,
            item.signal.published_at or item.signal.collected_at,
        ),
        reverse=True,
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _cluster(ranked: list[RankedSignal]) -> list[list[RankedSignal]]:
    clusters: list[list[RankedSignal]] = []
    cluster_tokens: list[frozenset[str]] = []
    for item in ranked:
        similarities = [_jaccard(item.tokens, tokens) for tokens in cluster_tokens]
        best = max(range(len(similarities)), key=similarities.__getitem__) if similarities else -1
        if best >= 0 and similarities[best] >= 0.16:
            clusters[best].append(item)
            cluster_tokens[best] = frozenset(cluster_tokens[best] | item.tokens)
        else:
            clusters.append([item])
            cluster_tokens.append(item.tokens)
    return sorted(
        clusters,
        key=lambda items: (
            len(items),
            sum(item.quality for item in items) / len(items),
        ),
        reverse=True,
    )


def _representatives(ranked: list[RankedSignal], limit: int) -> list[RankedSignal]:
    selected: list[RankedSignal] = []
    seen: set[str] = set()
    best_by_type: dict[str, RankedSignal] = {}
    for item in ranked:
        best_by_type.setdefault(item.signal.source_type, item)
    for item in sorted(best_by_type.values(), key=lambda entry: entry.quality, reverse=True):
        if len(selected) >= limit:
            break
        selected.append(item)
        seen.add(item.signal.signal_id)
    for item in ranked:
        if len(selected) >= limit:
            break
        if item.signal.signal_id not in seen:
            selected.append(item)
            seen.add(item.signal.signal_id)
    return selected


def _one_sentence(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    sentence = re.split(r"(?<=[.!?])\s+", normalized, maxsplit=1)[0]
    return sentence[:limit]


def build_discovery_signal_context(
    root: Path,
    *,
    compact: bool = False,
) -> DiscoverySignalContext:
    max_age_days = int(os.getenv("MAX_SIGNAL_AGE_DAYS", "180"))
    ranked = _rank_signals(root, max_age_days=max_age_days)
    representative_limit = 6 if compact else 12
    cluster_limit = 4 if compact else 8
    summary_limit = 90 if compact else 180
    clusters = _cluster(ranked)
    frequency_by_signal = {
        item.signal.signal_id: len(cluster) for cluster in clusters for item in cluster
    }
    representative_ranking = sorted(
        ranked,
        key=lambda item: (
            min(frequency_by_signal.get(item.signal.signal_id, 1), 10),
            item.quality,
            item.recency,
        ),
        reverse=True,
    )
    representatives = _representatives(representative_ranking, representative_limit)
    representative_payload = [
        {
            "signal_id": item.signal.signal_id,
            "title_or_summary": _one_sentence(
                item.signal.title or item.signal.summary, summary_limit
            ),
            "source_type": item.signal.source_type,
            "quality_score": item.quality,
            "published_at": (
                item.signal.published_at or item.signal.collected_at
            ).isoformat(),
            "duplicate_cluster_id": item.duplicate_cluster_id,
        }
        for item in representatives
    ]

    clusters_payload: list[dict[str, object]] = []
    for items in clusters[:cluster_limit]:
        member_ids = [item.signal.signal_id for item in items]
        cluster_hash = hashlib.sha256("|".join(sorted(member_ids)).encode()).hexdigest()[:12]
        clusters_payload.append(
            {
                "cluster_id": f"cluster-{cluster_hash}",
                "problem_description": _one_sentence(
                    items[0].signal.summary or items[0].signal.title,
                    summary_limit,
                ),
                "signal_ids": member_ids[: (8 if compact else 16)],
                "unique_source_count": len({item.signal.url for item in items}),
                "source_types": sorted({item.signal.source_type for item in items}),
                "average_quality_score": round(
                    sum(item.quality for item in items) / len(items), 4
                ),
                "frequency": len(items),
            }
        )
    included_ids = {item.signal.signal_id for item in representatives}
    return DiscoverySignalContext(
        representative_signals=representative_payload,
        signal_clusters=clusters_payload,
        total_unique_signals=len(ranked),
        included_signal_count=len(included_ids),
        excluded_signal_count=max(0, len(ranked) - len(included_ids)),
    )


def estimate_input_tokens_from_chars(chars: int) -> int:
    return math.ceil(max(chars, 0) / 3)
