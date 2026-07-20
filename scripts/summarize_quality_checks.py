from __future__ import annotations

import os
from pathlib import Path

from agents.quality import summarize_check_results

CHECKS = (
    ("pytest", "PYTEST_RESULT"),
    ("ruff", "RUFF_RESULT"),
    ("workflow_validation", "WORKFLOW_RESULT"),
    ("npm_ci", "NPM_CI_RESULT"),
    ("eslint", "ESLINT_RESULT"),
    ("typecheck", "TYPECHECK_RESULT"),
    ("vitest", "VITEST_RESULT"),
    ("production_build", "BUILD_RESULT"),
    ("python_dependency_audit", "PIP_AUDIT_RESULT"),
    ("npm_dependency_audit", "NPM_AUDIT_RESULT"),
    ("security_scan", "SECURITY_RESULT"),
)


def main() -> int:
    status, failed_check = summarize_check_results(
        [(name, os.getenv(variable, "skipped")) for name, variable in CHECKS]
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"validation_status={status}\n")
            handle.write(f"failed_check={failed_check}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
