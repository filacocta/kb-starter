from __future__ import annotations

import json
from datetime import date

import pytest

from kb_feed.dry_run import LIVE_KB_ROOT, dry_run_update, parse_topic_map
from kb_feed.errors import KbFeedError


def _resolver(address: str):
    def resolve(*_args):
        return [(None, None, None, None, (address, 0))]

    return resolve


def _kb_fixture(tmp_path):
    kb_root = tmp_path / "kb"
    topic_dir = kb_root / "topics" / "obsidian"
    topic_dir.mkdir(parents=True)
    (topic_dir / "reading-list.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Obsidian Reading List\n\n"
        "## Queue\n",
        encoding="utf-8",
    )
    (kb_root / "_dive-queue.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Dive Queue\n\n"
        "## obsidian\n",
        encoding="utf-8",
    )
    return kb_root


def test_parse_topic_map() -> None:
    assert parse_topic_map(["123=obsidian"]) == {123: "obsidian"}


def test_parse_topic_map_rejects_bad_values() -> None:
    with pytest.raises(KbFeedError):
        parse_topic_map(["obsidian"])


def test_dry_run_update_writes_telegram_text_link_to_fixture(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("kb_feed.dry_run.date", _FixedDate)
    kb_root = _kb_fixture(tmp_path)
    update_path = tmp_path / "update.json"
    update_path.write_text(
        json.dumps(
            {
                "update_id": 1,
                "message": {
                    "message_id": 99,
                    "message_thread_id": 123,
                    "text": "Read this",
                    "entities": [
                        {
                            "type": "text_link",
                            "offset": 0,
                            "length": 4,
                            "url": "https://www.example.com/path/?utm_source=telegram&a=1#frag",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    result = dry_run_update(update_path, kb_root, {123: "obsidian"}, resolver=_resolver("93.184.216.34"))

    assert result.urls_written == ("https://example.com/path?a=1",)
    line = "- [ ] https://example.com/path?a=1 — Read this — (via Telegram #obsidian, 2026-05-28)"
    assert line in (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_dry_run_refuses_live_kb_root(tmp_path) -> None:
    update_path = tmp_path / "update.json"
    update_path.write_text('{"message": {"message_thread_id": 1, "text": ""}}', encoding="utf-8")

    with pytest.raises(KbFeedError):
        dry_run_update(update_path, LIVE_KB_ROOT, {1: "obsidian"})


def test_dry_run_requires_thread_mapping(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    update_path = tmp_path / "update.json"
    update_path.write_text('{"message": {"message_thread_id": 123, "text": ""}}', encoding="utf-8")

    with pytest.raises(KbFeedError):
        dry_run_update(update_path, kb_root, {})


def test_parse_topic_map_rejects_path_traversal_slug() -> None:
    with pytest.raises(KbFeedError):
        parse_topic_map(["123=../../outside"])


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 28)
