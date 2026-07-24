import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.report_materializer import (
    materialize_report,
    report_artifact_path,
    report_operation_metadata,
    report_period,
    stable_report_operation_key,
    stable_report_operation_key_hash,
)
from agents.schemas import ActionEnvelope, CompanyState, LifecycleStage


def _write_repo(root):
    (root / "company").mkdir(parents=True)
    (root / "company/state.json").write_text(
        CompanyState(lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK).model_dump_json()
        + "\n"
    )
    (root / "company/strategy.json").write_text(
        json.dumps({"review": {"timezone": "Asia/Seoul"}}) + "\n"
    )


def _report_action() -> ActionEnvelope:
    return ActionEnvelope.model_validate(
        {
            "role": "researcher",
            "action_type": "write_report",
            "title": "보고서 작성",
            "summary": "주간 운영 보고서를 작성합니다.",
            "rationale": "운영 판단을 공유할 필요가 있습니다.",
            "risk_level": "low",
            "requires_approval": False,
            "evidence_ids": [],
            "report": {
                "problem_id": "problem-001",
                "report_type": "weekly",
                "title": "주간 운영 보고서",
                "summary": "모델이 요청한 파일명 대신 trusted 경로로 저장됩니다.",
                "period_summary": "이번 주 운영 상태와 판단 근거를 요약합니다.",
                "sections": [
                    {
                        "heading": "핵심 판단",
                        "content": "운영 상태를 바탕으로 다음 검토 항목을 정리합니다.",
                    }
                ],
                "evidence_ids": [],
            },
        }
    )


def test_trusted_weekly_report_path_uses_operating_timezone(tmp_path):
    _write_repo(tmp_path)
    now = datetime(2026, 7, 23, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    assert report_period(tmp_path, now=now) == "2026-W30"
    assert report_artifact_path("2026-W30") == "reports/weekly_report_2026-W30.pdf"

    change = materialize_report(_report_action(), tmp_path, now=now)

    assert change.path == "reports/weekly_report_2026-W30.pdf"
    assert change.content.startswith("%PDF-")
    assert "2023_10" not in change.path


def test_report_operation_key_is_stable_without_run_specific_values(tmp_path):
    _write_repo(tmp_path)
    state = CompanyState(lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK)
    now = datetime(2026, 7, 23, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    metadata = report_operation_metadata(tmp_path, state, now=now)

    assert metadata["operation_key"] == stable_report_operation_key(
        lifecycle_stage="DISTRIBUTION_CHECK",
        report_type="weekly",
        report_period_value="2026-W30",
        active_problem_id=None,
    )
    assert "run" not in str(metadata["operation_key"]).lower()
    assert metadata["operation_key_hash"] == stable_report_operation_key_hash(
        str(metadata["operation_key"])
    )
    assert metadata["artifact_path"] == "reports/weekly_report_2026-W30.pdf"
    later = report_operation_metadata(
        tmp_path,
        state,
        now=datetime(2026, 7, 24, 23, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert later["operation_key"] == metadata["operation_key"]
    assert later["operation_key_hash"] == metadata["operation_key_hash"]


def test_materialized_report_inserts_trusted_problem_context(tmp_path):
    _write_repo(tmp_path)
    (tmp_path / "research/problems").mkdir(parents=True)
    (tmp_path / "research/problems/problem-001.json").write_text(
        json.dumps(
            {
                "problem_id": "problem-001",
                "title": "Inventory list navigation",
                "target_users": ["inventory operators"],
                "description": "Operators lose their place in long inventory lists.",
                "current_workaround": "Manual scrolling and memory.",
                "evidence_ids": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK,
            active_problem_id="problem-001",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    change = materialize_report(_report_action(), tmp_path)

    assert "Inventory list navigation" in change.content
    assert "inventory operators" in change.content


def test_materialized_report_does_not_require_model_problem_title_or_summary(tmp_path):
    _write_repo(tmp_path)
    (tmp_path / "research/problems").mkdir(parents=True)
    (tmp_path / "research/problems/problem-001.json").write_text(
        json.dumps(
            {
                "problem_id": "problem-001",
                "title": "재고 목록 위치 복귀 문제",
                "target_users": ["재고 관리자", "창고 운영자"],
                "description": "긴 재고 목록에서 특정 위치나 항목으로 이동하기 어렵습니다.",
                "current_workaround": "스크롤과 수동 기억을 조합합니다.",
                "evidence_ids": [],
                "evidence": [],
                "frequency_score": 7,
                "severity_score": 6,
                "buildability_score": 8,
                "confidence": 0.8,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK,
            active_problem_id="problem-001",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    payload = _report_action().model_dump(mode="json")
    payload["report"].pop("title")
    payload["report"].pop("summary")
    payload["report"]["findings"] = ["이번 기간에는 위치 복귀 문제가 반복됐습니다."]
    payload["report"]["recommendations"] = ["목록 이동 부담을 줄이는 실험을 준비합니다."]
    action = ActionEnvelope.model_validate(payload)

    change = materialize_report(action, tmp_path)

    assert "재고 목록 위치 복귀 문제" in change.content
    assert "재고 관리자, 창고 운영자" in change.content
    assert "재고 관리 시스템" in change.content
    assert "긴 재고 목록에서 특정 위치나 항목으로 이동하기 어렵습니다." in change.content
    assert "이번 기간에는 위치 복귀 문제가 반복됐습니다." in change.content


def test_materialized_report_ignores_model_supplied_untrusted_domain_text(tmp_path):
    _write_repo(tmp_path)
    (tmp_path / "research/problems").mkdir(parents=True)
    (tmp_path / "research/problems/problem-001.json").write_text(
        json.dumps(
            {
                "problem_id": "problem-001",
                "title": "재고 목록 탐색 문제",
                "target_users": ["재고 관리자"],
                "description": "긴 재고 목록에서 위치를 잃는 문제가 있습니다.",
                "current_workaround": "수동으로 위치를 기억합니다.",
                "evidence_ids": [],
                "evidence": [],
                "frequency_score": 7,
                "severity_score": 6,
                "buildability_score": 8,
                "confidence": 0.8,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "company/state.json").write_text(
        CompanyState(
            lifecycle_stage=LifecycleStage.DISTRIBUTION_CHECK,
            active_problem_id="problem-001",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    payload = _report_action().model_dump(mode="json")
    payload["report"]["title"] = "게임 내 탐색 보고서"
    payload["report"]["summary"] = "커뮤니티 선택 드롭다운 문제를 요약합니다."
    action = ActionEnvelope.model_validate(payload)

    change = materialize_report(action, tmp_path)

    assert "재고 목록 탐색 문제" in change.content
    assert "재고 관리자" in change.content
    assert "게임 내 탐색 보고서" not in change.content
    assert "커뮤니티 선택 드롭다운 문제" not in change.content
