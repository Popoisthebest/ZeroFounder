from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def check_candidate_workflows(root: Path) -> None:
    workflows = root / ".github/workflows"
    if not workflows.is_dir():
        raise ValueError("candidate workflow 디렉터리가 없습니다. [missing_workflow_directory]")
    found = False
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        found = True
        if path.is_symlink():
            raise ValueError(f"workflow 심볼릭 링크는 금지됩니다: {path.name} [symlink]")
        text = path.read_text(encoding="utf-8")
        if "pull_request_target" in text:
            raise ValueError(f"금지된 pull_request_target이 있습니다: {path.name}")
        document = yaml.safe_load(text)
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
            raise ValueError(f"workflow 문서 형식이 잘못됐습니다: {path.name}")
        if document.get("permissions") != {"contents": "read"}:
            raise ValueError(f"workflow 기본 권한이 안전하지 않습니다: {path.name}")
        for name, job in document["jobs"].items():
            if not isinstance(job, dict):
                raise ValueError(f"workflow job 형식이 잘못됐습니다: {path.name}:{name}")
            permissions = job.get("permissions", {})
            if not isinstance(permissions, dict):
                raise ValueError(f"job 권한 형식이 잘못됐습니다: {path.name}:{name}")
            write_scopes = {scope for scope, value in permissions.items() if value == "write"}
            if len(write_scopes) > 2:
                raise ValueError(f"job 쓰기 권한이 과도합니다: {path.name}:{name}")
    if not found:
        raise ValueError("candidate workflow 파일이 없습니다. [missing_workflows]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        check_candidate_workflows(args.root.resolve())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(str(exc)) from exc
    print("후보 workflow YAML 및 권한 검사가 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
