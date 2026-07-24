from scripts.write_workflow_outcome_summary import render_summary


def test_workflow_outcome_summary_distinguishes_rejected_action(monkeypatch):
    monkeypatch.setenv("MODEL_RESULT", "success")
    monkeypatch.setenv("MODEL_ACTION_TYPE", "write_report")
    monkeypatch.setenv("VALIDATED_ACTION_TYPE", "no_op")
    monkeypatch.setenv("MODEL_ACCEPTED", "false")
    monkeypatch.setenv("FAILURE_STAGE", "schema_validation")
    monkeypatch.setenv("REJECTION_CODE", "problem_context_mismatch")
    monkeypatch.setenv("CORRECTION_ATTEMPTED", "true")
    monkeypatch.setenv("CORRECTION_RESPONSE_WAS_REQUEST_ECHO", "false")
    monkeypatch.setenv("DEPENDENCY_APPROVAL_REQUIRED", "false")
    monkeypatch.setenv("DEPENDENCY_APPROVAL_CREATED", "false")
    monkeypatch.setenv("BRANCH_CREATED", "false")
    monkeypatch.setenv("PR_CREATED", "false")
    monkeypatch.setenv("QUALITY_GATE_EXECUTED", "false")

    summary = render_summary()

    assert "action_rejected" in summary
    assert "model_action_rejected" in summary
    assert "problem_context_mismatch" in summary
    assert "| branch_created | false |" in summary
    assert "| pr_created | false |" in summary
    assert "| quality_gate_executed | false |" in summary
    assert "| dependency_approval_created | false |" in summary
