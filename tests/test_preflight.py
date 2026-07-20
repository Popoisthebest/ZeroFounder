from agents.preflight import build_preflight_decision, checkpoint_after_material_work
from agents.schemas import RepositoryCheckpoint, TriggerReason


def test_unchanged_preflight_is_no_op_and_checkpoint_unchanged():
    checkpoint = RepositoryCheckpoint(last_product_sha="abc", last_metrics_hash="m1")
    decision = build_preflight_decision(
        checkpoint=checkpoint,
        signal_quality={},
        issue_ids=[],
        comment_ids=[],
        product_sha="abc",
        metrics_hash="m1",
        due_experiment=False,
        daily_review_due=False,
        weekly_review_due=False,
        manual=False,
        min_new_signals=5,
        strong_evidence_threshold=0.85,
    )
    assert not decision.should_call_model
    assert checkpoint_after_material_work(checkpoint, decision) == checkpoint


def test_manual_and_strong_signal_trigger():
    decision = build_preflight_decision(
        checkpoint=RepositoryCheckpoint(),
        signal_quality={"signal-001": 0.9},
        issue_ids=[],
        comment_ids=[],
        product_sha=None,
        metrics_hash=None,
        due_experiment=False,
        daily_review_due=False,
        weekly_review_due=False,
        manual=True,
        min_new_signals=5,
        strong_evidence_threshold=0.85,
    )
    assert TriggerReason.STRONG_SIGNAL in decision.reasons
    assert TriggerReason.MANUAL in decision.reasons
