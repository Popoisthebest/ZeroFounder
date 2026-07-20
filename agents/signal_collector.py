from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from agents.schemas import MarketSignal, SignalPack, SignalSource, SignalSourceConfig

USER_AGENT = "ZeroFounder/1.0 (+https://github.com/)"
TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref"}


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        sorted(
            (key, val) for key, val in parse_qsl(parts.query) if key.lower() not in TRACKING_KEYS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalize_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:2000]


def signal_id(url: str, title: str) -> str:
    digest = hashlib.sha256(
        f"{canonical_url(url)}\n{normalize_text(title).lower()}".encode()
    ).hexdigest()
    return f"signal-{digest[:16]}"


class SignalCollector:
    def __init__(
        self,
        config: SignalSourceConfig,
        *,
        token: str | None = None,
        repository: str | None = None,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.config = config
        self.token = token
        self.repository = repository
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.Client(timeout=20, headers=headers)
        self.sleep = sleep
        self.errors: list[dict[str, str]] = []

    def _enabled_packs(self) -> list[SignalPack]:
        enabled = set(self.config.enabled_packs)
        return [pack for pack in self.config.packs if pack.enabled and pack.pack_id in enabled]

    def collect(self, inbox: Path | None = None) -> list[MarketSignal]:
        output: list[MarketSignal] = []
        for pack in self._enabled_packs():
            for source in pack.sources:
                if not source.enabled:
                    continue
                try:
                    output.extend(self._collect_source(pack.pack_id, source, inbox))
                except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
                    self.errors.append({"source_id": source.source_id, "error": str(exc)[:500]})
                self.sleep(1.0)
        deduped: dict[str, MarketSignal] = {}
        for signal in output:
            deduped.setdefault(signal.signal_id, signal)
        return list(deduped.values())[:100]

    def _collect_source(
        self, pack_id: str, source: SignalSource, inbox: Path | None
    ) -> list[MarketSignal]:
        if source.adapter == "github_search":
            data = self._get_json("https://api.github.com/search/issues", {"q": source.query or ""})
            return self._from_github_items(pack_id, source, data.get("items", []))
        if source.adapter == "github_repo_search":
            data = self._get_json(
                "https://api.github.com/search/repositories",
                {"q": source.query or "", "sort": "stars", "order": "desc"},
            )
            return self._from_github_items(pack_id, source, data.get("items", []))
        if source.adapter == "repository_issues":
            if not self.repository:
                return []
            data = self._get_json(
                f"https://api.github.com/repos/{self.repository}/issues",
                {"state": "all", "per_page": str(source.max_items)},
            )
            return self._from_github_items(pack_id, source, data)
        if source.adapter == "hacker_news":
            return self._hacker_news(pack_id, source)
        if source.adapter == "rss":
            return self._rss(pack_id, source)
        if source.adapter == "github_discussions":
            return self._discussions(pack_id, source)
        if source.adapter == "inbox" and inbox:
            return self._inbox(pack_id, source, inbox)
        return []

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _make_signal(
        self,
        pack_id: str,
        source: SignalSource,
        *,
        url: str,
        title: str,
        summary: str,
        published_at: datetime | None = None,
    ) -> MarketSignal:
        clean_url = canonical_url(url)
        clean_title = normalize_text(title)[:300]
        clean_summary = normalize_text(summary)
        return MarketSignal(
            signal_id=signal_id(clean_url, clean_title),
            source_pack=pack_id,
            source_type=source.source_type,
            url=clean_url,
            title=clean_title,
            summary=clean_summary or clean_title,
            collected_at=datetime.now(UTC),
            published_at=published_at,
            content_hash=hashlib.sha256(f"{clean_title}\n{clean_summary}".encode()).hexdigest(),
        )

    def _from_github_items(
        self, pack_id: str, source: SignalSource, items: list[dict[str, Any]]
    ) -> list[MarketSignal]:
        signals = []
        for item in items[: source.max_items]:
            url = item.get("html_url")
            title = item.get("title") or item.get("full_name") or item.get("name")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            summary = (
                item.get("body")
                or item.get("bodyText")
                or item.get("description")
                or title
            )
            signals.append(
                self._make_signal(
                    pack_id,
                    source,
                    url=url,
                    title=title,
                    summary=str(summary)[:2000],
                )
            )
            self.sleep(0.2)
        return signals

    def _hacker_news(self, pack_id: str, source: SignalSource) -> list[MarketSignal]:
        ids = self._get_json("https://hacker-news.firebaseio.com/v0/newstories.json")
        signals = []
        for item_id in ids[: source.max_items]:
            item = self._get_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
            if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
                continue
            url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
            title = item.get("title") or "Hacker News discussion"
            signals.append(
                self._make_signal(
                    pack_id,
                    source,
                    url=str(url),
                    title=str(title),
                    summary=str(item.get("text") or title),
                    published_at=datetime.fromtimestamp(item["time"], UTC)
                    if item.get("time")
                    else None,
                )
            )
        return signals

    def _rss(self, pack_id: str, source: SignalSource) -> list[MarketSignal]:
        if not source.url:
            return []
        response = self.client.get(source.url)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        signals = []
        for entry in feed.entries[: source.max_items]:
            url = entry.get("link")
            title = entry.get("title")
            if not url or not title:
                continue
            signals.append(
                self._make_signal(
                    pack_id,
                    source,
                    url=str(url),
                    title=str(title),
                    summary=str(entry.get("summary") or title),
                )
            )
        return signals

    def _discussions(self, pack_id: str, source: SignalSource) -> list[MarketSignal]:
        if not self.token:
            return []
        query = """
        query($owner:String!, $name:String!) {
          repository(owner:$owner, name:$name) {
            discussions(first:25, orderBy:{field:UPDATED_AT,direction:DESC}) {
              nodes { title bodyText url createdAt }
            }
          }
        }
        """
        signals: list[MarketSignal] = []
        for repository in source.repositories[:5]:
            owner, name = repository.split("/", 1)
            response = self.client.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": {"owner": owner, "name": name}},
            )
            response.raise_for_status()
            nodes = (
                response.json()
                .get("data", {})
                .get("repository", {})
                .get("discussions", {})
                .get("nodes", [])
            )
            signals.extend(self._from_github_items(pack_id, source, nodes))
            self.sleep(0.2)
        return signals[: source.max_items]

    def _inbox(self, pack_id: str, source: SignalSource, inbox: Path) -> list[MarketSignal]:
        signals = []
        for path in sorted(inbox.glob("*"))[: source.max_items]:
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json"}:
                continue
            text = path.read_text(errors="replace")[:8000]
            url = (
                f"https://github.com/{self.repository}/blob/main/research/inbox/{path.name}"
                if self.repository
                else f"https://local.invalid/research/{path.name}"
            )
            signals.append(
                self._make_signal(
                    pack_id,
                    source,
                    url=url,
                    title=path.stem,
                    summary=text,
                )
            )
        return signals


def load_config(path: Path) -> SignalSourceConfig:
    return SignalSourceConfig.model_validate_json(path.read_text())


def append_new_signals(root: Path, signals: list[MarketSignal]) -> int:
    existing_ids: set[str] = set()
    raw = root / "signals/raw"
    raw.mkdir(parents=True, exist_ok=True)
    for path in raw.glob("*.jsonl"):
        for line in path.read_text(errors="replace").splitlines():
            try:
                existing_ids.add(json.loads(line)["signal_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    new = [signal for signal in signals if signal.signal_id not in existing_ids]
    if not new:
        return 0
    destination = raw / f"{datetime.now(UTC).date().isoformat()}.jsonl"
    with destination.open("a", encoding="utf-8") as handle:
        for signal in new:
            handle.write(signal.model_dump_json() + "\n")
    return len(new)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    collector = SignalCollector(
        load_config(root / "signals/sources.json"),
        token=os.getenv("GITHUB_TOKEN"),
        repository=os.getenv("GITHUB_REPOSITORY"),
    )
    count = append_new_signals(root, collector.collect(root / "research/inbox"))
    print(json.dumps({"new_signals": count, "source_errors": collector.errors}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
