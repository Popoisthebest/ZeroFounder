from __future__ import annotations

import os


def main() -> int:
    validation_status = os.getenv("VALIDATION_STATUS", "quality_check_not_started")
    record_result = os.getenv("RECORD_RESULT", "")
    if record_result != "success":
        raise SystemExit("품질검사 결과를 PR에 기록하지 못했습니다.")
    if validation_status != "passed":
        raise SystemExit(f"품질검사 최종 gate 실패: {validation_status}")
    print("품질검사와 PR 상태 기록이 모두 완료됐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
