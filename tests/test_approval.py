import pytest

from agents.approval import (
    founder_result_counts_as_validation,
    parse_approval_command,
    validate_human_founder_result,
)
from agents.schemas import DependencyProposal
from scripts.create_dependency_issue import MARKER
from scripts.process_issue_command import dependency_from_body


def test_commands_must_be_exact_and_injection_is_ignored():
    assert parse_approval_command("/approve") == "approve"
    assert parse_approval_command("please /approve") is None
    assert parse_approval_command("/approve\n$(curl attacker)") is None


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
