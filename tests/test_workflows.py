from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_workflow_yaml_and_permissions():
    workflows = {}
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflows[path.name] = yaml.safe_load(path.read_text())
    assert workflows
    agent = workflows["agent.yml"]
    assert agent["permissions"] == {"contents": "read"}
    dispatch = agent["jobs"]["dispatch-quality-check"]
    assert dispatch["permissions"] == {"actions": "write", "contents": "read"}
    actions_writers = [
        name
        for name, job in agent["jobs"].items()
        if job.get("permissions", {}).get("actions") == "write"
    ]
    assert actions_writers == ["dispatch-quality-check"]
    assert "pull_request_target" not in (ROOT / ".github/workflows/agent.yml").read_text()
    quality = workflows["quality-check.yml"]
    assert "workflow_dispatch" in quality[True]
    deploy = workflows["deploy.yml"]
    assert deploy["permissions"] == {"contents": "read"}
    assert deploy["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }


def test_founder_results_is_protected_from_model_patch():
    from agents.safety import ALWAYS_PROTECTED

    assert "founder/results.json" in ALWAYS_PROTECTED
