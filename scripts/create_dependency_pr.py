from __future__ import annotations

import argparse
import os

from agents.github_client import GitHubClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    default = str(client.repository_info().get("default_branch") or "main")
    pull = client.create_pull_request(
        title=f"chore(deps): 승인된 의존성 {args.package} 추가",
        body=f"""## 변경 목적

창업자가 승인한 의존성 변경을 적용합니다.

## 생성된 산출물

- 승인 제안: `{args.proposal}`
- 검증 commit: `{args.sha}`

## 사용한 근거

- 권한이 확인된 창업자의 `/approve` 명령

## 상태 전환

- 없음

## 검증 결과

- 신뢰된 설치 스크립트의 audit·test·lint·build를 통과했습니다.

## 위험 및 제한 사항

- 자동 병합하지 않습니다.

## 창업자 확인 사항

- 패키지 버전, 라이선스, 보안 영향을 최종 검토하세요.

<!-- zerofounder-ci-status -->
## 품질검사 상태

- 상태: **품질검사 시작 안 됨** (`quality_check_not_started`)
""",
        head=args.branch,
        base=default,
    )
    client.add_labels(int(pull["number"]), ["agent-generated", "tool-request"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"pr_number={int(pull['number'])}\n")
        handle.write(f"default_branch={default}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
