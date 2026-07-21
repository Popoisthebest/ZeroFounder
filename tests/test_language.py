import re

from agents.language import (
    GLOBAL_MARKET_POLICY,
    can_migrate_agent_generated,
    choose_product_ui_language,
    localized_signal_fields,
    migrated_korean_content,
    operating_language,
)
from agents.operating_output import (
    action_commit_message,
    render_agent_pull_request,
    render_dependency_issue,
)
from agents.schemas import ActionEnvelope, ActionType


def problem_action() -> ActionEnvelope:
    return ActionEnvelope.model_validate(
        {
            "role": "researcher",
            "action_type": "create_problem_candidate",
            "title": "문제 후보 생성",
            "summary": "반복되는 인수인계 정보 손실 문제를 구조화합니다.",
            "rationale": "서로 독립된 공개 근거에서 동일한 수작업이 관찰됐습니다.",
            "risk_level": "low",
            "requires_approval": False,
            "evidence_ids": ["signal-001"],
            "problem_candidate": {
                "problem_id": "problem-handoff",
                "title": "오픈소스 유지보수 인수인계 정보 손실",
                "target_users": ["글로벌 오픈소스 유지보수자"],
                "description": (
                    "유지보수자 교체 때 판단 배경과 반복 업무가 "
                    "여러 도구에 흩어져 사라집니다."
                ),
                "current_workaround": "Issue, 문서, 채팅을 수동으로 대조합니다.",
            },
            "state_transition": {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
        }
    )


def dependency_action() -> ActionEnvelope:
    return ActionEnvelope.model_validate(
        {
            "role": "builder",
            "action_type": "propose_dependency",
            "title": "의존성 추가 제안",
            "summary": "검증된 YAML 파서가 필요합니다.",
            "rationale": "표준 라이브러리에는 YAML 파서가 없습니다.",
            "risk_level": "medium",
            "requires_approval": True,
            "evidence_ids": [],
            "dependency_proposal": {
                "proposal_id": "dependency-001",
                "ecosystem": "python",
                "package_name": "PyYAML",
                "exact_version": "6.0.2",
                "dependency_type": "development",
                "reason": "workflow 문법을 규칙 기반으로 검증합니다.",
                "standard_library_alternative": "완전한 대체 기능이 없습니다.",
                "license": "MIT",
                "security_risk": "승인된 정확한 버전만 설치하고 audit합니다.",
                "bundle_or_maintenance_impact": "제품 번들에는 포함되지 않습니다.",
                "requested_by_action": "propose_dependency",
                "status": "proposed",
            },
        }
    )


def test_operating_language_defaults_to_korean(monkeypatch):
    monkeypatch.delenv("OPERATING_LANGUAGE", raising=False)
    assert operating_language() == "ko"


def test_korean_pull_request_keeps_english_machine_contracts():
    action = problem_action()
    title, body = render_agent_pull_request(action, "a" * 40)
    assert title.startswith("feat(agent): ")
    assert "문제 후보 생성" in title
    for heading in {
        "## 변경 목적",
        "## 생성된 산출물",
        "## 사용한 근거",
        "## 상태 전환",
        "## 검증 결과",
        "## 위험 및 제한 사항",
        "## 창업자 확인 사항",
    }:
        assert heading in body
    dumped = action.model_dump(mode="json", by_alias=True)
    assert dumped["action_type"] == "create_problem_candidate"
    assert dumped["state_transition"] == {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"}


def test_pr_body_rejects_unvalidated_english_action_text():
    action = problem_action().model_copy(
        update={
            "title": "Create a problem candidate",
            "summary": "Create one evidence-backed problem candidate.",
            "rationale": "Stored signals show a repeated manual workflow.",
        }
    )
    try:
        render_agent_pull_request(action, "a" * 40)
    except ValueError as exc:
        assert "language_mismatch" in str(exc)
    else:
        raise AssertionError("English action text should not be rendered into a PR body")


def test_korean_dependency_issue_and_english_commands():
    title, body = render_dependency_issue(dependency_action())
    assert title.startswith("[창업자 승인 요청]")
    assert "## 요청 내용" in body
    assert "/approve" in body
    assert "승인" in body


def test_foreign_signal_preserves_original_and_korean_layers():
    localized = localized_signal_fields(
        "Maintainer handoff loses context",
        "Teams reconstruct decisions from issues and chat.",
    )
    assert localized["original_language"] == "en"
    assert localized["original_title"] == "Maintainer handoff loses context"
    assert localized["original_summary"].startswith("Teams reconstruct")
    assert len(localized_signal_fields("Title", "x" * 800)["original_summary"]) == 500
    assert re.search(r"[가-힣]", localized["korean_title"])
    assert re.search(r"[가-힣]", localized["korean_summary"])
    assert localized["market_region"] == ["global"]
    assert localized["translation_confidence"] == 0.0


def test_korean_output_does_not_prefer_korean_market():
    assert GLOBAL_MARKET_POLICY["research_scope"] == "global"
    assert GLOBAL_MARKET_POLICY["output_language_is_market_preference"] is False
    assert GLOBAL_MARKET_POLICY["region_neutral_evaluation"] is True


def test_product_ui_language_is_independent_from_operating_language(monkeypatch):
    monkeypatch.setenv("OPERATING_LANGUAGE", "ko")
    assert operating_language() == "ko"
    assert choose_product_ui_language(["ja"]) == "ja"
    assert choose_product_ui_language(["en", "de"]) == "en"


def test_only_agent_generated_bot_items_can_be_migrated():
    bot_item = {
        "number": 7,
        "labels": [{"name": "agent-generated"}],
        "user": {"login": "github-actions[bot]"},
    }
    user_item = {
        "number": 8,
        "labels": [{"name": "agent-generated"}],
        "user": {"login": "human-founder"},
    }
    assert can_migrate_agent_generated(bot_item)
    assert not can_migrate_agent_generated(user_item)
    title, body = migrated_korean_content(bot_item, is_pull_request=True)
    assert title.startswith("chore(agent):")
    assert "한국어" in body


def test_commit_prefix_is_english_and_description_is_korean():
    message = action_commit_message(problem_action(), "1234")
    assert message.startswith("feat(discovery):")
    assert "문제 후보" in message
    assert message.endswith("[run:1234]")
    assert ActionType.CREATE_PROBLEM_CANDIDATE.value in {
        item.value for item in ActionType
    }
