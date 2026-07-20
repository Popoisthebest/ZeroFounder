import json
from pathlib import Path

import httpx

from agents.schemas import SignalSourceConfig
from agents.signal_collector import SignalCollector, append_new_signals, canonical_url


def test_canonical_url_removes_tracking():
    assert (
        canonical_url("HTTPS://Example.COM/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"
    )


def test_source_failure_does_not_abort_other_sources(tmp_path: Path):
    config = SignalSourceConfig.model_validate(
        {
            "enabled_packs": ["developer"],
            "packs": [
                {
                    "pack_id": "developer",
                    "sources": [
                        {
                            "source_id": "bad",
                            "source_type": "rss",
                            "adapter": "rss",
                            "url": "https://bad.test",
                            "reliability": 0.5,
                        },
                        {
                            "source_id": "good",
                            "source_type": "github_issue",
                            "adapter": "github_search",
                            "query": "pain",
                            "reliability": 0.7,
                        },
                    ],
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bad.test":
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "html_url": "https://github.com/o/r/issues/1",
                        "title": "Manual workaround",
                        "body": "Repeated pain",
                    }
                ]
            },
        )

    collector = SignalCollector(
        config, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    signals = collector.collect(tmp_path)
    assert len(signals) == 1
    assert collector.errors[0]["source_id"] == "bad"


def test_append_deduplicates_between_runs(tmp_path: Path):
    config = SignalSourceConfig.model_validate_json(
        (Path(__file__).parents[1] / "signals/sources.json").read_text()
    )
    source = config.packs[0].sources[0]
    collector = SignalCollector(config, sleep=lambda _: None)
    signal = collector._make_signal(
        "developer", source, url="https://example.com/p", title="Pain", summary="Pain"
    )
    assert append_new_signals(tmp_path, [signal]) == 1
    assert append_new_signals(tmp_path, [signal]) == 0
    stored = list((tmp_path / "signals/raw").glob("*.jsonl"))[0]
    assert len([json.loads(line) for line in stored.read_text().splitlines()]) == 1
