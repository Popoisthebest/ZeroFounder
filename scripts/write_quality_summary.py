from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    result_path = Path(os.environ["QUALITY_RESULT_PATH"])
    result = json.loads(result_path.read_text())
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return 0
    content = f"""## ZeroFounder 품질 검사 결과

- 검증 상태: `{result['validation_status']}`
- 검증 SHA: `{result['verified_sha'] or '없음'}`
- 실패 검사: `{result['failed_check'] or '없음'}`
- 실행 URL: {result['quality_run_url'] or '확인 불가'}

검증 대상은 전달받은 PR head SHA와 일치하는 경우에만 checkout했습니다.
"""
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
