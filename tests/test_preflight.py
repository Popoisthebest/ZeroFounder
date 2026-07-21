from agents.preflight import (
    build_preflight_decision,
    checkpoint_after_material_work,
    usage_allows_run,
)
from agents.schemas import ActionType, PreflightDecision, RepositoryCheckpoint, TriggerReason
from scripts.write_preflight_summary import render_summary


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


def test_create_idea_checkpoint_updates_only_idempotency_key():
    checkpoint = RepositoryCheckpoint(
        last_signal_ids=["signal-old"],
        idempotency_keys=["a" * 64],
        last_metrics_hash="b" * 64,
    )
    decision = PreflightDecision(
        should_call_model=True,
        reasons=[TriggerReason.MANUAL, TriggerReason.METRICS_CHANGED],
        new_signal_ids=["signal-ee24e3790220b151"],
        metrics_hash="c" * 64,
        idempotency_key="d" * 64,
    )

    updated = checkpoint_after_material_work(
        checkpoint,
        decision,
        action_type=ActionType.CREATE_IDEA_CANDIDATES,
    )

    assert updated.last_signal_ids == ["signal-old"]
    assert updated.last_metrics_hash == "b" * 64
    assert updated.idempotency_keys == ["a" * 64, "d" * 64]


def test_material_work_changes_metrics_hash_only_for_metrics_trigger():
    checkpoint = RepositoryCheckpoint(last_metrics_hash="b" * 64)
    same_metrics_decision = PreflightDecision(
        should_call_model=True,
        reasons=[TriggerReason.MANUAL],
        metrics_hash="c" * 64,
        idempotency_key="d" * 64,
    )
    changed_metrics_decision = PreflightDecision(
        should_call_model=True,
        reasons=[TriggerReason.METRICS_CHANGED],
        metrics_hash="c" * 64,
        idempotency_key="e" * 64,
    )

    assert (
        checkpoint_after_material_work(checkpoint, same_metrics_decision).last_metrics_hash
        == "b" * 64
    )
    assert (
        checkpoint_after_material_work(checkpoint, changed_metrics_decision).last_metrics_hash
        == "c" * 64
    )


def test_usage_gate_allows_limit_equality():
    assert usage_allows_run(
        completed_calls=6,
        active_reservations=0,
        required_calls=2,
        daily_limit=8,
    )


def test_usage_gate_blocks_only_when_sum_exceeds_limit():
    assert not usage_allows_run(
        completed_calls=7,
        active_reservations=0,
        required_calls=2,
        daily_limit=8,
    )


def test_usage_summary_contains_required_limit_fields():
    decision = build_preflight_decision(
        checkpoint=RepositoryCheckpoint(),
        signal_quality={},
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
    decision.completed_calls_today = 6
    decision.required_calls = 2
    decision.daily_limit = 8
    decision.usage_calculation = "6 + 0 + 2 <= 8"
    summary = render_summary(decision)
    for field in {
        "오늘 완료된 호출",
        "활성 예약",
        "이번 실행 필요 호출",
        "일일 한도",
        "호출 허용",
        "한도 계산식",
    }:
        assert f"| {field} |" in summary
