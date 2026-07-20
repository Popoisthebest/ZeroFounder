from __future__ import annotations

import argparse
import json
import os

from agents.github_client import GitHubClient
from agents.language import can_migrate_agent_generated, migrated_korean_content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    candidates = []
    for item in client.open_issues_and_pulls():
        if not can_migrate_agent_generated(item):
            continue
        is_pull = isinstance(item.get("pull_request"), dict)
        title, body = migrated_korean_content(item, is_pull_request=is_pull)
        number = int(item["number"])
        candidates.append({"number": number, "is_pull_request": is_pull})
        if not args.apply:
            continue
        if is_pull:
            client.update_pull_request(number, title=title, body=body)
        else:
            client.update_issue(number, title=title, body=body)
    print(json.dumps({"apply": args.apply, "candidates": candidates}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
