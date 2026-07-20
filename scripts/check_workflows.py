from __future__ import annotations

from pathlib import Path

import yaml


def main() -> int:
    root = Path(__file__).parents[1]
    workflows = root / ".github/workflows"
    actions_writers: list[tuple[str, str]] = []
    agent_document: dict | None = None
    for path in workflows.glob("*.yml"):
        text = path.read_text()
        if "pull_request_target" in text:
            raise SystemExit(f"forbidden pull_request_target in {path.name}")
        document = yaml.safe_load(text)
        if path.name == "agent.yml":
            agent_document = document
        if document.get("permissions") != {"contents": "read"}:
            raise SystemExit(f"workflow default permission must be contents: read: {path.name}")
        for name, job in document.get("jobs", {}).items():
            permissions = job.get("permissions", {})
            if permissions.get("actions") == "write":
                actions_writers.append((path.name, name))
            write_scopes = {scope for scope, value in permissions.items() if value == "write"}
            if len(write_scopes) > 2:
                raise SystemExit(f"excessive job permissions: {path.name}:{name}")
    if actions_writers:
        raise SystemExit(f"unexpected actions:write jobs: {actions_writers}")
    if agent_document is None:
        raise SystemExit("agent workflow is missing")
    steps = agent_document["jobs"]["create-branch"]["steps"]

    def step_index(predicate) -> int:
        return next(index for index, step in enumerate(steps) if predicate(step))

    try:
        checkout = step_index(
            lambda step: str(step.get("uses", "")).startswith("actions/checkout@")
        )
        branch = step_index(lambda step: step.get("id") == "prepare_branch")
        apply = step_index(lambda step: step.get("id") == "apply")
        tests = step_index(lambda step: step.get("run") == "python -m pytest")
        commit = step_index(lambda step: step.get("id") == "commit")
    except StopIteration as exc:
        raise SystemExit("create-branch is missing a required guarded step") from exc
    if not checkout < branch < apply < tests < commit:
        raise SystemExit("create-branch must branch, apply, test, then commit/push")
    if steps[tests].get("continue-on-error"):
        raise SystemExit("create-branch Pytest failures must stop before push")
    agent_text = (workflows / "agent.yml").read_text()
    if "dispatch-quality-check" in agent_text or "quality-dispatch-result" in agent_text:
        raise SystemExit("agent workflow must not dispatch or download cross-run quality artifacts")
    quality = yaml.safe_load((workflows / "quality-check.yml").read_text())
    triggers = quality.get(True, {})
    if "workflow_call" not in triggers or "workflow_dispatch" not in triggers:
        raise SystemExit("quality-check must support workflow_call and workflow_dispatch")
    call_outputs = triggers["workflow_call"].get("outputs", {})
    required_outputs = {"validation_status", "verified_sha", "failed_check", "quality_run_url"}
    if not required_outputs.issubset(call_outputs):
        raise SystemExit("quality-check reusable outputs are incomplete")
    record = agent_document["jobs"].get("record-quality-status", {})
    if "always()" not in str(record.get("if", "")):
        raise SystemExit("quality result recording must run after failures")
    print("workflow syntax and permissions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
