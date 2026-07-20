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
    }

    quality_steps = quality["jobs"]["quality"]["steps"]
    checkout = next(
        step
        for step in quality_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ needs.verify-head.outputs.verified_sha }}"
    verify_command = quality["jobs"]["verify-head"]["steps"][-1]["run"]
    assert "--branch \"$AGENT_BRANCH\"" in verify_command
    assert "--sha \"$COMMIT_SHA\"" in verify_command


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

    gate = agent["jobs"]["quality-gate"]
    assert "always()" in gate["if"]
    assert {"quality-check", "record-quality-status"}.issubset(gate["needs"])


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
