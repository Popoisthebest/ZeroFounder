import hashlib
import json

import pytest

import agents.orchestrator as orchestrator
from agents.preflight import (
    build_preflight_decision,
    checkpoint_after_material_work,
    usage_allows_run,
)
from agents.schemas import (
    ActionType,
    CompanyState,
    LifecycleStage,
    PreflightDecision,
    RepositoryCheckpoint,
    TriggerReason,
)
from scripts.write_preflight_summary import render_summary


def _write_preflight_root(
    root,
    *,
    checkpoint: RepositoryCheckpoint | None = None,
    state: CompanyState | None = None,
):
    metrics_body = "{}\n"
    default_checkpoint = RepositoryCheckpoint(
        last_metrics_hash=hashlib.sha256(metrics_body.encode()).hexdigest()
    )
    (root / "company").mkdir(parents=True, exist_ok=True)
    (root / "company/checkpoints.json").write_text(
        (checkpoint or default_checkpoint).model_dump_json() + "\n"
    )
    (root / "company/state.json").write_text(
        (state or CompanyState()).model_dump_json() + "\n"
    )
    (root / "company/metrics.json").write_text(metrics_body)
    (root / "company/strategy.json").write_text(
        json.dumps(
            {
                "review": {
                    "daily_hour": 23,
                    "weekly_day": 7,
                    "weekly_hour": 23,
                },
                "evidence": {
                    "min_new_signals_for_analysis": 5,
                    "strong_evidence_threshold": 0.85,
                    "min_unique_signals": 2,
                },
            }
        )
        + "\n"
    )


def _write_issue_comment_event(
    root,
    body: str,
    *,
    actor: str = "founder",
    user_type: str = "User",
    labels: list[str] | None = None,
):
    event = {
        "issue": {
            "id": 10,
            "number": 5,
            "labels": [{"name": name} for name in (labels or ["requires-approval"])],
        },
        "comment": {
            "id": 20,
            "body": body,
            "user": {"login": actor, "type": user_type},
        },
    }
    path = root / "event.json"
    path.write_text(json.dumps(event))
    return path


class _PreflightClient:
    can_write = True
    open_pulls: list[dict] = []
    usage = {
        "completed_inference_calls": 0,
        "reserved_inference_calls": 0,
        "failed_after_request_calls": 0,
        "skipped_runs": 0,
    }

    def __init__(self, token: str, repository: str):
        pass

    def has_write_permission(self, actor: str) -> bool:
        return self.can_write

    def model_usage_today(self):
        return self.usage

    def open_agent_pull_requests(self):
        return self.open_pulls


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
    assert decision.skip_reason == "no_new_trigger"
    assert checkpoint_after_material_work(checkpoint, decision) == checkpoint


def test_issue_comment_duplicate_is_skipped_before_model_flow(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    event_path = _write_issue_comment_event(tmp_path, "Duplicate of #5")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, event_path, "issue_comment")

    assert decision["should_call_model"] is False
    assert decision["blocked_reason"] == "unrecognized_comment_command"
    assert decision["skip_reason"] == "unrecognized_comment_command"
    assert decision["skip_detail"]
    assert decision["comment_ids"] == []
    summary = render_summary(PreflightDecision.model_validate(decision))
    assert "| skipped | true |" in summary
    assert "| skip_reason | unrecognized_comment_command |" in summary
    assert "| skip_detail |" in summary


def test_issue_comment_bot_is_skipped_before_model_flow(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    event_path = _write_issue_comment_event(
        tmp_path,
        "/run-agent",
        actor="github-actions[bot]",
        user_type="Bot",
        labels=["agent-generated"],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, event_path, "issue_comment")

    assert decision["should_call_model"] is False
    assert decision["blocked_reason"] == "bot_comment"


def test_issue_comment_approve_is_not_model_flow(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    event_path = _write_issue_comment_event(tmp_path, "/approve")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, event_path, "issue_comment")

    assert decision["should_call_model"] is False
    assert decision["blocked_reason"] == "command_handled_by_approval_flow"


def test_issue_comment_run_agent_opens_model_flow_only_for_authorized_command(
    tmp_path,
    monkeypatch,
):
    _write_preflight_root(tmp_path)
    event_path = _write_issue_comment_event(
        tmp_path,
        "/run-agent",
        labels=["agent-generated"],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, event_path, "issue_comment")

    assert decision["should_call_model"] is True
    assert decision["blocked_reason"] is None
    assert decision["comment_ids"] == [20]
    assert "manual" in decision["reasons"]


def test_issue_comment_run_agent_requires_write_permission(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    event_path = _write_issue_comment_event(
        tmp_path,
        "/run-agent",
        labels=["agent-generated"],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    _PreflightClient.can_write = False
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, event_path, "issue_comment")

    assert decision["should_call_model"] is False
    assert decision["blocked_reason"] == "unauthorized_actor"
    _PreflightClient.can_write = True


def test_schedule_without_trigger_records_no_new_trigger(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, None, "schedule")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "no_new_trigger"
    assert decision["skip_detail"]
    assert decision["lifecycle_stage"] == "DISCOVERY"
    assert decision["active_problem_id"] is None
    assert decision["new_signal_ids"] == []
    assert decision["schedule_cron"] == "17 */2 * * *"


def test_same_stage_agent_pr_blocks_model_flow(tmp_path, monkeypatch):
    state = CompanyState(
        lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
        active_problem_id="problem-001",
    )
    _write_preflight_root(tmp_path, state=state)
    _PreflightClient.open_pulls = [
        {
            "number": 12,
            "labels": [{"name": "agent-generated"}],
            "pull_request": {},
            "body": (
                '<!-- zerofounder-agent-pr {"active_problem_id":"problem-001",'
                '"lifecycle_stage":"IDEA_EVALUATION"} -->'
            ),
        }
    ]
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, None, "workflow_dispatch")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "open_agent_pr_exists"
    assert decision["open_agent_pr_count"] == 1
    assert decision["open_agent_pr_numbers"] == [12]
    _PreflightClient.open_pulls = []


def test_different_problem_agent_pr_does_not_block(tmp_path, monkeypatch):
    state = CompanyState(
        lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
        active_problem_id="problem-001",
    )
    _write_preflight_root(tmp_path, state=state)
    _PreflightClient.open_pulls = [
        {
            "number": 13,
            "labels": [{"name": "agent-generated"}],
            "pull_request": {},
            "body": (
                '<!-- zerofounder-agent-pr {"active_problem_id":"problem-002",'
                '"lifecycle_stage":"IDEA_EVALUATION"} -->'
            ),
        }
    ]
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, None, "workflow_dispatch")

    assert decision["should_call_model"] is True
    assert decision["open_agent_pr_count"] == 0
    _PreflightClient.open_pulls = []


def test_idempotency_key_seen_blocks_manual_run(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)
    first = orchestrator.preflight(tmp_path, None, "workflow_dispatch")
    checkpoint = RepositoryCheckpoint(
        idempotency_keys=[first["idempotency_key"]],
        last_metrics_hash=first["metrics_hash"],
    )
    _write_preflight_root(tmp_path, checkpoint=checkpoint)

    decision = orchestrator.preflight(tmp_path, None, "workflow_dispatch")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "idempotency_key_already_processed"
    assert decision["idempotency_key_seen"] is True


def test_founder_approval_stage_blocks_model_flow(tmp_path, monkeypatch):
    _write_preflight_root(
        tmp_path,
        state=CompanyState(lifecycle_stage=LifecycleStage.FOUNDER_APPROVAL),
    )
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, None, "workflow_dispatch")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "approval_required"


def test_concurrent_reservation_blocks_material_work(tmp_path, monkeypatch):
    _write_preflight_root(tmp_path)
    _PreflightClient.usage = {
        "completed_inference_calls": 0,
        "reserved_inference_calls": 1,
        "failed_after_request_calls": 0,
        "skipped_runs": 0,
    }
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(orchestrator, "GitHubClient", _PreflightClient)

    decision = orchestrator.preflight(tmp_path, None, "workflow_dispatch")

    assert decision["should_call_model"] is False
    assert decision["skip_reason"] == "concurrent_run_active"
    assert decision["concurrent_run_detected"] is True
    _PreflightClient.usage = {
        "completed_inference_calls": 0,
        "reserved_inference_calls": 0,
        "failed_after_request_calls": 0,
        "skipped_runs": 0,
    }


def test_skipped_preflight_requires_reason_and_detail():
    with pytest.raises(ValueError, match="skip_reason"):
        PreflightDecision(should_call_model=False, idempotency_key="a" * 64)

    with pytest.raises(ValueError, match="skip_detail"):
        PreflightDecision(
            should_call_model=False,
            idempotency_key="a" * 64,
            skip_reason="no_new_trigger",
        )


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
