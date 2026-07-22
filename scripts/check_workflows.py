from __future__ import annotations

import argparse
from pathlib import Path

import yaml

PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2}
MIN_ACTION_MAJOR = {
    "actions/checkout": 7,
    "actions/setup-node": 7,
    "actions/setup-python": 7,
    "actions/upload-artifact": 7,
    "actions/download-artifact": 8,
}


def check_action_version(uses: object, *, workflow_name: str) -> None:
    value = str(uses or "")
    if "@" not in value:
        return
    action, version = value.split("@", 1)
    minimum = MIN_ACTION_MAJOR.get(action)
    if minimum is None:
        return
    if not version.startswith("v") or not version[1:].split(".", 1)[0].isdigit():
        raise SystemExit(f"action major를 고정해야 합니다: {workflow_name}:{value}")
    major = int(version[1:].split(".", 1)[0])
    if major < minimum:
        raise SystemExit(
            f"deprecated action major입니다: {workflow_name}:{value} < {action}@v{minimum}"
        )


def iter_steps(document: dict) -> list[dict]:
    steps: list[dict] = []
    for job in document.get("jobs", {}).values():
        for step in job.get("steps", []):
            if isinstance(step, dict):
                steps.append(step)
    return steps


def max_job_permissions(document: dict) -> dict[str, str]:
    required: dict[str, str] = {}
    for job in document.get("jobs", {}).values():
        permissions = job.get("permissions", {})
        if not isinstance(permissions, dict):
            raise SystemExit("job 권한은 scope별 dict여야 합니다.")
        for scope, level in permissions.items():
            if level not in PERMISSION_LEVELS:
                raise SystemExit(f"알 수 없는 job 권한 수준입니다: {scope}:{level}")
            current = required.get(scope, "none")
            if PERMISSION_LEVELS[level] > PERMISSION_LEVELS[current]:
                required[scope] = level
    return required


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    workflows = root / ".github/workflows"
    actions_writers: list[tuple[str, str]] = []
    agent_document: dict | None = None
    for path in workflows.glob("*.yml"):
        text = path.read_text()
        if "pull_request_target" in text:
            raise SystemExit(f"금지된 pull_request_target이 있습니다: {path.name}")
        document = yaml.safe_load(text)
        if path.name == "agent.yml":
            agent_document = document
        for step in iter_steps(document):
            check_action_version(step.get("uses"), workflow_name=path.name)
        if document.get("permissions") != {"contents": "read"}:
            raise SystemExit(f"workflow 기본 권한은 contents: read여야 합니다: {path.name}")
        for name, job in document.get("jobs", {}).items():
            permissions = job.get("permissions", {})
            if not isinstance(permissions, dict):
                raise SystemExit(f"job 권한은 scope별 dict여야 합니다: {path.name}:{name}")
            if permissions.get("actions") == "write":
                actions_writers.append((path.name, name))
            write_scopes = {scope for scope, value in permissions.items() if value == "write"}
            if len(write_scopes) > 2:
                raise SystemExit(f"job 쓰기 권한이 과도합니다: {path.name}:{name}")
    if actions_writers:
        raise SystemExit(f"예상하지 않은 actions:write job입니다: {actions_writers}")
    if agent_document is None:
        raise SystemExit("agent workflow가 없습니다.")
    concurrency = agent_document.get("concurrency", {})
    if concurrency != {
        "group": "zerofounder-agent-${{ github.repository }}-${{ github.ref }}",
        "cancel-in-progress": False,
    }:
        raise SystemExit("agent workflow concurrency group/cancel 정책이 올바르지 않습니다.")
    schema_text = (root / "agents/schemas.py").read_text()
    if (
        "skipped preflight requires skip_reason" not in schema_text
        or "skipped preflight requires skip_detail" not in schema_text
    ):
        raise SystemExit("skipped=true인 preflight는 skip_reason과 skip_detail을 강제해야 합니다.")
    orchestrator_text = (root / "agents/orchestrator.py").read_text()
    if (
        "unrecognized_comment_command" not in orchestrator_text
        or "parse_comment_command" not in orchestrator_text
    ):
        raise SystemExit("일반 issue_comment는 모델 실행을 열면 안 됩니다.")
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
        raise SystemExit("create-branch에 필수 보호 단계가 없습니다.") from exc
    if not checkout < branch < apply < tests < commit:
        raise SystemExit("create-branch는 branch, apply, test, commit/push 순서여야 합니다.")
    if steps[tests].get("continue-on-error"):
        raise SystemExit("create-branch Pytest 실패는 push 전에 실행을 중단해야 합니다.")
    agent_text = (workflows / "agent.yml").read_text()
    if "dispatch-quality-check" in agent_text or "quality-dispatch-result" in agent_text:
        raise SystemExit("agent workflow는 다른 run의 품질검사 artifact를 조회하면 안 됩니다.")
    quality = yaml.safe_load((workflows / "quality-check.yml").read_text())
    triggers = quality.get(True, {})
    if "workflow_call" not in triggers or "workflow_dispatch" not in triggers:
        raise SystemExit("quality-check는 workflow_call과 workflow_dispatch를 지원해야 합니다.")
    call_outputs = triggers["workflow_call"].get("outputs", {})
    required_outputs = {
        "validation_status",
        "verified_sha",
        "failed_check",
        "quality_run_url",
        "rejection_code",
        "rejection_reason",
        "rejected_files",
        "allowed_files",
        "changed_files_count",
    }
    if not required_outputs.issubset(call_outputs):
        raise SystemExit("quality-check reusable output이 불완전합니다.")
    quality_jobs = quality.get("jobs", {})
    required_jobs = {
        "verify-head",
        "quality",
        "policy",
        "finalize",
        "manual-result",
        "manual-record",
    }
    if not required_jobs.issubset(quality_jobs):
        raise SystemExit("quality-check control/candidate job이 불완전합니다.")
    quality_call = agent_document["jobs"].get("quality-check", {})
    expected_quality_permissions = max_job_permissions(quality)
    if quality_call.get("permissions") != expected_quality_permissions:
        raise SystemExit(
            "agent quality-check 호출 권한은 reusable workflow job 권한 최대치와 일치해야 합니다."
        )
    expected_conditions = {
        "policy": "needs.verify-head.outputs.validation_status == 'valid'",
        "quality": (
            "needs.verify-head.outputs.validation_status == 'valid' && "
            "needs.policy.outputs.validation_status == 'valid'"
        ),
    }
    for job_name, expected_condition in expected_conditions.items():
        if quality_jobs[job_name].get("if") != expected_condition:
            raise SystemExit(
                f"candidate job은 신뢰된 PR·정책 검증 뒤에만 실행해야 합니다: {job_name}"
            )
        paths = {
            step.get("with", {}).get("path")
            for step in quality_jobs[job_name].get("steps", [])
            if str(step.get("uses", "")).startswith("actions/checkout@")
        }
        if paths != {"control", "candidate"}:
            raise SystemExit(f"품질검사 job은 control과 candidate를 분리해야 합니다: {job_name}")
    candidate_steps = [
        step for step in quality_jobs["quality"].get("steps", []) if step.get("id")
    ]
    if not candidate_steps or any(
        step.get("working-directory") != "candidate" for step in candidate_steps
    ):
        raise SystemExit("candidate 명령은 candidate 경로에서만 실행해야 합니다.")
    validation_step = next(
        (
            step
            for step in quality_jobs["policy"].get("steps", [])
            if step.get("id") == "candidate_validation"
        ),
        None,
    )
    if (
        not validation_step
        or validation_step.get("working-directory") != "control"
        or "scripts.validate_candidate_change" not in str(validation_step.get("run", ""))
    ):
        raise SystemExit("action별 candidate 검증은 신뢰된 control 코드로 실행해야 합니다.")
    result_step = next(
        (
            step
            for step in quality_jobs["finalize"].get("steps", [])
            if step.get("id") == "result"
        ),
        None,
    )
    if (
        not result_step
        or result_step.get("working-directory") != "control"
        or "scripts.summarize_quality_checks" not in str(result_step.get("run", ""))
    ):
        raise SystemExit("품질검사 집계는 신뢰된 control 코드를 사용해야 합니다.")
    record = agent_document["jobs"].get("record-quality-status", {})
    if "always()" not in str(record.get("if", "")):
        raise SystemExit("품질검사 결과 기록은 실패 후에도 실행해야 합니다.")
    print("workflow 문법과 권한 검사가 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
