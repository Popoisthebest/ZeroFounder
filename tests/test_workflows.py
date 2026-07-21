import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def load_workflows() -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }


def test_workflow_yaml_and_job_permissions():
    workflows = load_workflows()
    assert workflows
    for workflow in workflows.values():
        assert workflow["permissions"] == {"contents": "read"}

    agent = workflows["agent.yml"]
    assert not [
        name
        for name, job in agent["jobs"].items()
        if job.get("permissions", {}).get("actions") == "write"
    ]
    assert "pull_request_target" not in (ROOT / ".github/workflows/agent.yml").read_text()

    deploy = workflows["deploy.yml"]
    assert deploy["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }


def test_workflow_and_job_display_names_are_korean():
    for workflow in load_workflows().values():
        assert re.search(r"[가-힣]", workflow["name"])
        for job in workflow["jobs"].values():
            assert re.search(r"[가-힣]", job["name"])


def test_agent_uses_reusable_quality_workflow_outputs_without_cross_run_artifact():
    workflows = load_workflows()
    agent = workflows["agent.yml"]
    quality_job = agent["jobs"]["quality-check"]
    assert quality_job["uses"] == "./.github/workflows/quality-check.yml"
    assert quality_job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",
    }
    assert set(quality_job["with"]) == {
        "pull_request_number",
        "agent_branch",
        "commit_sha",
        "called_by_agent",
    }

    text = (ROOT / ".github/workflows/agent.yml").read_text(encoding="utf-8")
    for forbidden in {
        "dispatch-quality-check",
        "quality-dispatch-result",
        "record-dispatch-status",
        "quality-check-manual-result",
    }:
        assert forbidden not in text


def test_reusable_quality_workflow_contract_and_exact_sha_checkout():
    quality = load_workflows()["quality-check.yml"]
    triggers = quality[True]
    assert "workflow_dispatch" in triggers
    call = triggers["workflow_call"]
    assert set(call["inputs"]) >= {
        "pull_request_number",
        "agent_branch",
        "commit_sha",
    }
    assert all(call["inputs"][name]["required"] for name in {
        "pull_request_number",
        "agent_branch",
        "commit_sha",
    })
    assert set(call["outputs"]) >= {
        "validation_status",
        "verified_sha",
        "failed_check",
        "quality_run_url",
        "rejection_code",
        "rejection_reason",
        "rejected_files",
        "changed_files_count",
    }

    quality_job = quality["jobs"]["quality"]
    assert quality_job["if"] == (
        "needs.verify-head.outputs.validation_status == 'valid' && "
        "needs.policy.outputs.validation_status == 'valid'"
    )
    quality_steps = quality_job["steps"]
    checkouts = {
        step.get("with", {}).get("path"): step
        for step in quality_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    }
    assert set(checkouts) == {"control", "candidate"}
    assert checkouts["control"]["with"]["ref"] == (
        "${{ github.event.repository.default_branch }}"
    )
    assert checkouts["candidate"]["with"]["ref"] == (
        "${{ needs.verify-head.outputs.verified_sha }}"
    )
    assert checkouts["candidate"]["with"]["persist-credentials"] is False
    verify_command = quality["jobs"]["verify-head"]["steps"][-1]["run"]
    assert "--branch \"$AGENT_BRANCH\"" in verify_command
    assert "--sha \"$COMMIT_SHA\"" in verify_command


def test_candidate_commands_and_control_helpers_are_isolated():
    quality = load_workflows()["quality-check.yml"]
    candidate_steps = [
        step for step in quality["jobs"]["quality"]["steps"] if step.get("id")
    ]
    assert candidate_steps
    for step in candidate_steps:
        assert step["working-directory"] == "candidate"
        assert "env -u GITHUB_ENV -u GITHUB_PATH" in step["run"]
        assert "scripts.summarize_quality_checks" not in step["run"]
        assert "scripts.security_check" not in step["run"]
    assert "--ignore-scripts" in next(
        step["run"] for step in candidate_steps if step["id"] == "npm_ci"
    )
    assert "--disable-pip-version-check" in next(
        step["run"] for step in candidate_steps if step["id"] == "python_dependencies"
    )

    policy_steps = quality["jobs"]["policy"]["steps"]
    policy_commands = "\n".join(str(step.get("run", "")) for step in policy_steps)
    assert "scripts.validate_candidate_change" in policy_commands
    assert "scripts.check_candidate_workflows" in policy_commands
    assert "scripts.security_check" in policy_commands
    for step in policy_steps:
        if step.get("id") in {"workflow", "security"}:
            assert step["working-directory"] == "control"
            assert step["env"]["PYTHONPATH"] == "${{ github.workspace }}/control"

    result_step = next(
        step for step in quality["jobs"]["finalize"]["steps"] if step.get("id") == "result"
    )
    assert result_step["working-directory"] == "control"
    assert "scripts.summarize_quality_checks" in result_step["run"]
    assert "$GITHUB_WORKSPACE/runtime/quality-results" in result_step["run"]
    assert result_step["env"]["PYTHONPATH"] == "${{ github.workspace }}/control"


def test_invalid_pr_target_never_runs_candidate_jobs():
    quality = load_workflows()["quality-check.yml"]
    assert quality["jobs"]["policy"]["if"] == (
        "needs.verify-head.outputs.validation_status == 'valid'"
    )
    assert quality["jobs"]["quality"]["if"] == (
        "needs.verify-head.outputs.validation_status == 'valid' && "
        "needs.policy.outputs.validation_status == 'valid'"
    )
    verify_steps = quality["jobs"]["verify-head"]["steps"]
    assert not any(step.get("with", {}).get("path") == "candidate" for step in verify_steps)


def test_manual_dispatch_uses_the_same_control_candidate_jobs():
    quality = load_workflows()["quality-check.yml"]
    assert set(quality[True]["workflow_dispatch"]["inputs"]) == {
        "pull_request_number",
        "agent_branch",
        "commit_sha",
    }
    assert {
        "verify-head",
        "quality",
        "policy",
        "finalize",
        "manual-result",
        "manual-record",
    }.issubset(quality["jobs"])
    manual_steps = quality["jobs"]["manual-result"]["steps"]
    assert any(step.get("with", {}).get("path") == "control" for step in manual_steps)
    assert not any(step.get("with", {}).get("path") == "candidate" for step in manual_steps)
    manual_record = quality["jobs"]["manual-record"]
    assert "always()" in manual_record["if"]
    assert manual_record["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
    }


def test_quality_result_is_recorded_after_failure_and_final_gate_is_present():
    agent = load_workflows()["agent.yml"]
    record = agent["jobs"]["record-quality-status"]
    assert "always()" in record["if"]
    assert "quality-check" in record["needs"]
    record_env = next(
        step["env"]
        for step in record["steps"]
        if "VALIDATION_STATUS" in step.get("env", {})
    )
    assert "needs.quality-check.outputs.validation_status" in record_env["VALIDATION_STATUS"]
    assert "needs.quality-check.outputs.failed_check" in record_env["FAILED_CHECK"]
    assert "needs.quality-check.outputs.quality_run_url" in record_env["QUALITY_RUN_URL"]
    assert "needs.quality-check.outputs.rejection_code" in record_env["REJECTION_CODE"]
    assert "needs.quality-check.outputs.rejected_files" in record_env["REJECTED_FILES"]
    assert next(step for step in record["steps"] if step.get("id") == "record")[
        "working-directory"
    ] == "control"

    gate = agent["jobs"]["quality-gate"]
    assert "always()" in gate["if"]
    assert {"quality-check", "record-quality-status"}.issubset(gate["needs"])
    gate_command = next(step for step in gate["steps"] if "run" in step)
    assert gate_command["working-directory"] == "control"


def test_model_preflight_and_diagnostic_inputs_remain_wired():
    agent = load_workflows()["agent.yml"]
    model_steps = agent["jobs"]["model"]["steps"]
    model_commands = "\n".join(str(step.get("run", "")) for step in model_steps)
    assert "--preflight runtime/preflight.json" in model_commands
    assert "--diagnostics runtime/model-diagnostic.json" in model_commands
    assert "scripts.write_model_summary" in model_commands
    assert "모델 호출 1 확인 [inference-confirm-1]" in {
        step.get("name") for step in model_steps
    }
    assert "모델 호출 2 확인 [inference-confirm-2]" in {
        step.get("name") for step in model_steps
    }
    assert "모델 호출 없는 실행 기록 [inference-skipped]" in {
        step.get("name") for step in model_steps
    }
    model_env = next(step["env"] for step in model_steps if "env" in step)
    assert "MODEL_DIAGNOSTIC_MODE" in model_env
    assert "MAX_MODEL_INPUT_TOKENS" in model_env
    assert "MAX_INPUT_CHARS" in model_env
    assert "OPERATING_LANGUAGE" in model_env

    preflight_steps = agent["jobs"]["preflight"]["steps"]
    preflight_commands = "\n".join(str(step.get("run", "")) for step in preflight_steps)
    assert "scripts.write_preflight_summary" in preflight_commands


def test_founder_results_is_protected_from_model_patch():
    from agents.safety import ALWAYS_PROTECTED

    assert "founder/results.json" in ALWAYS_PROTECTED
