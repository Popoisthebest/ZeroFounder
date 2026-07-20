from datetime import date
from pathlib import Path

from agents.reporting import write_daily_report

ROOT = Path(__file__).parents[1]


def test_daily_report_is_not_duplicated(tmp_path: Path):
    for name in ["company", "venture", "signals/raw", "reports"]:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for source in [
        "company/state.json",
        "company/metrics.json",
        "company/task-board.json",
        "company/usage.json",
        "venture/venture.json",
    ]:
        destination = tmp_path / source
        destination.write_text((ROOT / source).read_text())
    today = date(2026, 7, 20)
    assert write_daily_report(tmp_path, today)
    assert not write_daily_report(tmp_path, today)
