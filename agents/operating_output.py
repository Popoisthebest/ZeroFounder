from __future__ import annotations

from agents.schemas import ActionEnvelope, ActionType, MaterializedActionEnvelope

ACTION_TITLES = {
    ActionType.CREATE_PROBLEM_CANDIDATE: "문제 후보 생성 및 근거 검증 단계 전환",
    ActionType.VALIDATE_EVIDENCE: "문제 후보 근거 검증",
    ActionType.CREATE_IDEA_CANDIDATES: "사업 아이템 후보 생성",
    ActionType.EVALUATE_IDEAS: "사업 아이템 후보 평가",
    ActionType.SELECT_IDEA: "사업 아이템 최종 선정",
    ActionType.CREATE_PRODUCT_SPEC: "MVP 제품 요구사항 작성",
    ActionType.CREATE_CODE_PATCH: "제품 기능 변경 제안",
    ActionType.CREATE_CONTENT: "운영 콘텐츠 생성",
    ActionType.CREATE_EXPERIMENT: "성장 실험 생성",
    ActionType.UPDATE_STRATEGY: "사업 전략 갱신",
    ActionType.WRITE_REPORT: "운영 보고서 작성",
}


def action_commit_message(action: ActionEnvelope | MaterializedActionEnvelope, run_id: str) -> str:
    description = ACTION_TITLES.get(action.action_type, "에이전트 운영 산출물 갱신")
    scope = "discovery" if action.action_type in {
        ActionType.CREATE_PROBLEM_CANDIDATE,
        ActionType.VALIDATE_EVIDENCE,
        ActionType.CREATE_IDEA_CANDIDATES,
        ActionType.EVALUATE_IDEAS,
    } else "agent"
    prefix = "feat" if action.action_type in ACTION_TITLES else "chore"
    return f"{prefix}({scope}): {description} [run:{run_id}]"


def render_agent_pull_request(
    action: ActionEnvelope | MaterializedActionEnvelope,
    sha: str,
) -> tuple[str, str]:
    description = ACTION_TITLES.get(action.action_type, "에이전트 운영 변경")
    title = f"feat(agent): {description}"
    evidence = ", ".join(f"`{item}`" for item in action.evidence_ids) or "없음"
    transition = (
        f"`{action.state_transition.from_stage.value}` → "
        f"`{action.state_transition.to_stage.value}`"
        if action.state_transition
        else "없음"
    )
    generated = "\n".join(f"- `{change.path}`" for change in action.files) or "- 없음"
    body = f"""## 변경 목적

{action.summary}

## 생성된 산출물

{generated}

## 사용한 근거

- 근거 ID: {evidence}
- 판단 근거: {action.rationale}

## 상태 전환

- {transition}

## 검증 결과

- create-branch 내부 고정 검사를 통과한 commit: `{sha}`
- reusable 품질검사 결과를 기다리는 중입니다.

## 위험 및 제한 사항

- 자동 병합하지 않습니다.
- 외부 게시, 결제, 계정 생성은 포함하지 않습니다.

## 창업자 확인 사항

- 변경 내용과 근거를 검토한 뒤 사람이 최종 결정합니다.

<!-- zerofounder-ci-status -->
## 품질검사 상태

- 상태: **품질검사 시작 안 됨** (`quality_check_not_started`)
"""
    return title, body


def render_dependency_issue(action: ActionEnvelope) -> tuple[str, str]:
    proposal = action.dependency_proposal
    if proposal is None:
        raise ValueError("dependency proposal is required")
    title = f"[창업자 승인 요청] 의존성 {proposal.package_name}@{proposal.exact_version} 추가"
    body = f"""## 요청 내용

`{proposal.package_name}@{proposal.exact_version}` 의존성 추가 승인을 요청합니다.

## 판단 근거

{action.summary}

{action.rationale}

## 대안

{proposal.standard_library_alternative}

## 위험 요소

- 라이선스: {proposal.license}
- 보안 위험: {proposal.security_risk}
- 번들 또는 유지보수 영향: {proposal.bundle_or_maintenance_impact}

## 승인 시 다음 단계

신뢰된 규칙 기반 스크립트가 정확한 버전을 설치하고 audit·test·lint·build 후 PR을 생성합니다.

## 사용 가능한 명령어

- `/approve`: 승인
- `/reject`: 거절
- `/revise`: 수정 요청
- `/pause`: 운영 일시정지
- `/resume`: 이전 단계로 복귀
- `/pivot`: 피벗 검토 요청
"""
    return title, body
