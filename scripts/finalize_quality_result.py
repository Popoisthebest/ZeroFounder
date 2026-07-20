from __future__ import annotations

import json
import os
from pathlib import Path

from agents.quality import finalize_validation_status


def main() -> int:
    status, failed_check = finalize_validation_status(
        verification_status=os.getenv("VERIFICATION_STATUS", ""),
        quality_job_result=os.getenv("QUALITY_JOB_RESULT", ""),
        quality_status=os.getenv("QUALITY_STATUS", ""),
        failed_check=os.getenv("FAILED_CHECK", ""),
    )
    verified_sha = os.getenv("VERIFIED_SHA", "")
    run_url = os.getenv("QUALITY_RUN_URL", "")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"validation_status={status}\n")
            handle.write(f"verified_sha={verified_sha}\n")
            handle.write(f"failed_check={failed_check}\n")
            handle.write(f"quality_run_url={run_url}\n")
    result_path = os.getenv("QUALITY_RESULT_PATH")
    if result_path:
        target = Path(result_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "validation_status": status,
                    "verified_sha": verified_sha,
                    "failed_check": failed_check,
                    "quality_run_url": run_url,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
