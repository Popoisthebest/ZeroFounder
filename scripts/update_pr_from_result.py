from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    status = data.get("status")
    if status not in {"ci_not_started", "awaiting_ci_approval"}:
        raise SystemExit("invalid dispatch result")
    return subprocess.run(
        ["python", "scripts/update_pr_status.py", "--pr", str(args.pr), "--status", status],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
