from __future__ import annotations

from pathlib import Path

import yaml


def main() -> int:
    root = Path(__file__).parents[1]
    workflows = root / ".github/workflows"
    actions_writers: list[tuple[str, str]] = []
    for path in workflows.glob("*.yml"):
        text = path.read_text()
        if "pull_request_target" in text:
            raise SystemExit(f"forbidden pull_request_target in {path.name}")
        document = yaml.safe_load(text)
        if document.get("permissions") != {"contents": "read"}:
            raise SystemExit(f"workflow default permission must be contents: read: {path.name}")
        for name, job in document.get("jobs", {}).items():
            permissions = job.get("permissions", {})
            if permissions.get("actions") == "write":
                actions_writers.append((path.name, name))
            write_scopes = {scope for scope, value in permissions.items() if value == "write"}
            if len(write_scopes) > 2:
                raise SystemExit(f"excessive job permissions: {path.name}:{name}")
    if actions_writers != [("agent.yml", "dispatch-quality-check")]:
        raise SystemExit(f"unexpected actions:write jobs: {actions_writers}")
    print("workflow syntax and permissions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
