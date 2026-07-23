from __future__ import annotations

import json
import os
from pathlib import Path


def _rejected_files() -> list[str]:
    try:
        value = json.loads(os.getenv("REJECTED_FILES", "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _allowed_files() -> list[str]:
    try:
        value = json.loads(os.getenv("ALLOWED_FILES", "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def main() -> int:
    target = Path(os.environ["QUALITY_RESULT_PATH"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "validation_status": os.getenv(
                    "VALIDATION_STATUS", "quality_check_not_started"
                ),
                "verified_sha": os.getenv("VERIFIED_SHA", ""),
                "failed_check": os.getenv("FAILED_CHECK", ""),
                "quality_run_url": os.getenv("QUALITY_RUN_URL", ""),
                "rejection_code": os.getenv("REJECTION_CODE", ""),
                "rejection_reason": os.getenv("REJECTION_REASON", ""),
                "rejected_files": _rejected_files(),
                "allowed_files": _allowed_files(),
                "changed_files_count": int(os.getenv("CHANGED_FILES_COUNT", "0") or 0),
                "report_type": os.getenv("REPORT_TYPE", ""),
                "report_period": os.getenv("REPORT_PERIOD", ""),
                "artifact_path": os.getenv("ARTIFACT_PATH", ""),
                "operation_key": os.getenv("OPERATION_KEY", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
