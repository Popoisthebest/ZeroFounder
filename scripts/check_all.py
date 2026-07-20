from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).parents[1]
    commands = [
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        ["npm", "ci"],
        [sys.executable, "-m", "pytest"],
        ["ruff", "check", "agents", "tests", "scripts"],
        [sys.executable, "-m", "scripts.check_workflows"],
        ["npm", "run", "lint"],
        ["npm", "run", "typecheck"],
        ["npm", "test", "--", "--run"],
        ["npm", "run", "build"],
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--cache-dir",
            ".cache/pip-audit",
            "-r",
            "requirements.txt",
        ],
        ["npm", "audit", "--audit-level=high"],
        [sys.executable, "-m", "scripts.security_check"],
    ]
    for command in commands:
        print(f"+ {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
