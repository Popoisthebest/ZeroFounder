import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.report_materializer import (
    materialize_report,
    report_artifact_path,
    report_operation_metadata,
    report_period,
    stable_report_operation_key,
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
    assert metadata["artifact_path"] == "reports/weekly_report_2026-W30.pdf"
