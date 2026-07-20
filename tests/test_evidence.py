from datetime import UTC, datetime, timedelta

from agents.evidence import build_evidence, evidence_gate
from agents.schemas import EvidenceClassification, MarketSignal


def make_signal(signal_id: str, source_type: str, url: str) -> MarketSignal:
    return MarketSignal(
        signal_id=signal_id,
        source_pack="test",
        source_type=source_type,
        url=url,
        title="A concrete repeated manual workaround",
        summary="Users spend two hours every week reconciling the same records by hand.",
        collected_at=datetime.now(UTC),
        published_at=datetime.now(UTC) - timedelta(days=2),
        content_hash=signal_id,
    )


def test_quality_is_calculated_by_code():
    signal = make_signal("signal-001", "industry_rss", "https://example.com/1")
    evidence = build_evidence(
        signal,
        EvidenceClassification(evidence_id="signal-001", specificity="high", directness="direct"),
        source_reliability=0.8,
        max_age_days=180,
        corroboration=1.0,
    )
    assert evidence.quality_score > 0.8


def test_gate_requires_independent_external_sources():
    items = []
    for index, source in enumerate(["github_issue", "industry_rss", "user_research"], start=1):
        signal = make_signal(f"signal-{index:03}", source, f"https://example.com/{index}")
        items.append(
            build_evidence(
                signal,
                EvidenceClassification(
                    evidence_id=signal.signal_id, specificity="high", directness="direct"
                ),
                source_reliability=0.9,
                max_age_days=180,
                corroboration=1.0,
            )
        )
    passed, reasons = evidence_gate(items, min_items=3, min_source_types=2, min_quality=0.65)
    assert passed, reasons
    failed, reasons = evidence_gate(items[:1], min_items=3, min_source_types=2, min_quality=0.65)
    assert not failed
    assert "at least one non-GitHub source is required" in reasons
