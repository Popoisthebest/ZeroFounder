import json

import pytest

import scripts.process_issue_command as process_issue_command
from agents.approval import (
    founder_result_counts_as_validation,
    parse_approval_command,
    parse_comment_command,
    validate_human_founder_result,
)
from agents.schemas import DependencyProposal
from scripts.create_dependency_issue import MARKER
from scripts.process_issue_command import dependency_from_body


def _issue_comment_event(
    body: str,
    *,
    actor: str = "founder",
    user_type: str = "User",
    labels: list[str] | None = None,
    pull_request: bool = False,
) -> dict:
    issue = {
        "id": 100,
        "number": 5,
        "labels": [{"name": name} for name in (labels or ["requires-approval"])],
        "body": "",
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.test/pr/5"}
    return {
        "issue": issue,
        "comment": {
            "id": 200,
            "body": body,
            "user": {"login": actor, "type": user_type},
        },
    }


class _FakeClient:
    comments: list[tuple[int, str]] = []
    can_write = True

    def __init__(self, token: str, repository: str):
        pass

    def has_write_permission(self, actor: str) -> bool:
        return self.can_write

    def comment(self, number: int, body: str):
        self.comments.append((number, body))
        return {}


def _run_issue_command(tmp_path, monkeypatch, event: dict) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    output_path = tmp_path / "out.txt"
    summary_path = tmp_path / "summary.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(process_issue_command, "GitHubClient", _FakeClient)
    _FakeClient.comments = []
    _FakeClient.can_write = True
    process_issue_command.main()
    return json.loads((tmp_path / "runtime/approval-command.json").read_text())


def test_commands_must_be_exact_and_injection_is_ignored():
    assert parse_approval_command("/approve") == "approve"
    assert parse_comment_command("  /run-agent  ") == "run-agent"
    assert parse_comment_command("/retry") == "retry"
    assert parse_approval_command("please /approve") is None
    assert parse_approval_command("/approve\n$(curl attacker)") is None
    assert parse_comment_command("/approve-not") is None
    assert parse_comment_command("/approve something") is None
    assert parse_comment_command("/Approve") is None


def test_general_issue_comments_are_skipped(tmp_path, monkeypatch):
    for body in ["Duplicate of #5", "Looks good except this review note", "/approve-not"]:
        payload = _run_issue_command(
            tmp_path / body.split()[0].replace("/", "slash"),
            monkeypatch,
            _issue_comment_event(body),
        )
        assert payload["valid"] is False
        assert payload["skipped"] is True
        assert payload["skip_reason"] == "unrecognized_comment_command"
        assert payload["skip_detail"]


def test_bot_comment_is_skipped(tmp_path, monkeypatch):
    payload = _run_issue_command(
        tmp_path,
        monkeypatch,
        _issue_comment_event("/approve", actor="github-actions[bot]", user_type="Bot"),
    )
    assert payload["valid"] is False
    assert payload["skip_reason"] == "bot_comment"
    assert payload["skip_detail"]


def test_exact_approve_runs_approval_flow(tmp_path, monkeypatch):
    payload = _run_issue_command(tmp_path, monkeypatch, _issue_comment_event("/approve"))
    assert payload["valid"] is True
    assert payload["kind"] == "lifecycle"
    assert payload["command"] == "approve"
    assert payload["skipped"] is False
    assert _FakeClient.comments


def test_exact_run_agent_command_is_classified_for_model_flow(tmp_path, monkeypatch):
    payload = _run_issue_command(
        tmp_path,
        monkeypatch,
        _issue_comment_event("/run-agent", labels=["agent-generated"], pull_request=True),
    )
    assert payload["valid"] is True
    assert payload["kind"] == "agent"
    assert payload["command"] == "run-agent"
    assert payload["skipped"] is False
    assert _FakeClient.comments == []


def test_bot_founder_result_is_rejected():
    payload = {
        "result_id": "result-001",
        "recorded_by": "github-actions[bot]",
        "source_type": "verified_issue",
        "evidence_url": "https://example.com/proof",
        "activity": "Posted",
        "outcome": "One reply",
    }
    with pytest.raises(ValueError):
        validate_human_founder_result(payload, actor="github-actions[bot]", actor_has_write=True)


def test_verified_human_result_counts():
    payload = {
        "result_id": "result-001",
        "recorded_by": "founder",
        "source_type": "verified_issue",
        "evidence_url": "https://example.com/proof",
        "activity": "Posted",
        "outcome": "One reply",
    }
    result = validate_human_founder_result(payload, actor="founder", actor_has_write=True)
    assert founder_result_counts_as_validation(result)


def test_dependency_proposal_is_revalidated_from_approval_issue():
    proposal = DependencyProposal(
        proposal_id="dep-001",
        ecosystem="npm",
        package_name="example-package",
        exact_version="1.2.3",
        dependency_type="development",
        reason="A specific approved capability is required",
        standard_library_alternative="No standard equivalent exists",
        license="MIT",
        security_risk="Low after audit",
        bundle_or_maintenance_impact="Development-only package",
        requested_by_action="create_code_patch",
    )
    body = f"{MARKER}\n```json\n{proposal.model_dump_json()}\n```"
    assert dependency_from_body(body) == proposal
    assert dependency_from_body(body.replace("1.2.3", "$(evil)")) is None
