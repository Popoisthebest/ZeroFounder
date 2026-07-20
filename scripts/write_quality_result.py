from __future__ import annotations

import json
import os
from pathlib import Path


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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
