import json
from pathlib import Path

import pytest

from agents.action_executor import ActionExecutor
from agents.problem_materializer import materialize_problem_candidate
from agents.schemas import ActionEnvelope


def _write_signal(root: Path, signal_id: str = "signal-001") -> str:
    target = root / "signals/raw"
    target.mkdir(parents=True)
    record = {
        "signal_id": signal_id,
        "source_pack": "small-business",
        "source_type": "rss",
        "url": f"https://evidence.example/{signal_id}",
        "title": "Repeated reconciliation work",
        "summary": "Teams manually reconcile updates across spreadsheets and messages.",
        "collected_at": "2026-07-20T00:00:00Z",
        "published_at": "2026-07-19T00:00:00Z",
        "content_hash": "a" * 64,
    }
    (target / "signals.jsonl").write_text(json.dumps(record) + "\n")
    return signal_id


def _action(signal_id: str, **overrides) -> ActionEnvelope:
    payload = {
        "role": "researcher",
        "action_type": "create_problem_candidate",
        "title": "Problem candidate",
        "summary": "Create one evidence-backed problem candidate.",
        "rationale": "The stored signal describes repeated manual work.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": [signal_id],
        "problem_candidate": {
            "problem_id": "problem-001",
            "title": "Repeated manual reconciliation",
            "target_users": ["small teams"],
            "description": "Small teams repeatedly reconcile the same updates by hand.",
            "current_workaround": "They combine spreadsheets, screenshots, and messages.",
        },
    }
    payload.update(overrides)
    return ActionEnvelope.model_validate(payload)


def test_problem_file_is_materialized_from_stored_evidence(tmp_path: Path):
    signal_id = _write_signal(tmp_path)
    change = materialize_problem_candidate(_action(signal_id), tmp_path)
    stored = json.loads(change.content)
    assert change.path == "research/problems/problem-001.json"
    assert stored["evidence_ids"] == [signal_id]
    assert stored["evidence"][0]["url"] == f"https://evidence.example/{signal_id}"
    assert isinstance(stored["frequency_score"], int)
    assert isinstance(stored["confidence"], float)


def test_executor_prepares_rule_based_file_without_model_path(tmp_path: Path):
    signal_id = _write_signal(tmp_path)
    prepared = ActionExecutor(tmp_path).prepare(_action(signal_id))
    assert [change.path for change in prepared.files] == [
        "research/problems/problem-001.json"
    ]


def test_unknown_evidence_id_rejects_materialization(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown evidence ids"):
        materialize_problem_candidate(_action("signal-missing"), tmp_path)


def test_model_supplied_problem_file_path_is_rejected(tmp_path: Path):
    signal_id = _write_signal(tmp_path)
    action = _action(
        signal_id,
        files=[{"path": "research/problems/model-choice.json", "content": "{}"}],
    )
    with pytest.raises(ValueError, match="model-provided file paths"):
        materialize_problem_candidate(action, tmp_path)
