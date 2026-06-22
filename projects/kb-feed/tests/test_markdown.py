from __future__ import annotations

from datetime import date

import pytest

from kb_feed.errors import KbWriteRejected
from kb_feed.markdown import append_topic_reading_list, telegram_annotation


def test_append_topic_reading_list_requires_kb_bot_author(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: brendan\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "## Queue\n",
        encoding="utf-8",
    )

    with pytest.raises(KbWriteRejected):
        append_topic_reading_list(path, "- [ ] https://example.com")


def test_append_topic_reading_list_is_idempotent_and_updates_modified(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "## Queue\n\n"
        "## Done\n",
        encoding="utf-8",
    )
    line = "- [ ] https://example.com/source — (via Telegram #obsidian, 2026-05-28)"

    assert append_topic_reading_list(path, line, today=date(2026, 5, 28))
    assert not append_topic_reading_list(path, line, today=date(2026, 5, 28))

    text = path.read_text(encoding="utf-8")
    assert "modified: 2026-05-28" in text
    assert text.count(line) == 1
    assert text.index(line) < text.index("## Done")


def test_append_topic_reading_list_dedupes_by_exact_line_not_substring(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "## Queue\n\n"
        "- [ ] https://example.com/abc\n",
        encoding="utf-8",
    )

    assert append_topic_reading_list(path, "- [ ] https://example.com/a", today=date(2026, 5, 28))

    text = path.read_text(encoding="utf-8")
    assert "- [ ] https://example.com/abc" in text
    assert "- [ ] https://example.com/a\n" in text


def test_append_topic_reading_list_uses_pending_heading(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "## Pending\n\n"
        "## Processed\n",
        encoding="utf-8",
    )
    line = "- [ ] https://example.com/pending"

    assert append_topic_reading_list(path, line, today=date(2026, 5, 28))

    text = path.read_text(encoding="utf-8")
    assert text.index("## Pending") < text.index(line) < text.index("## Processed")


def test_append_topic_reading_list_uses_queue_substring_heading(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "## Approved Exact URL Queue\n\n"
        "## Failed\n",
        encoding="utf-8",
    )
    line = "- [ ] https://example.com/approved"

    assert append_topic_reading_list(path, line, today=date(2026, 5, 28))

    text = path.read_text(encoding="utf-8")
    assert text.index("## Approved Exact URL Queue") < text.index(line) < text.index("## Failed")


def test_append_topic_reading_list_creates_pending_heading(tmp_path) -> None:
    path = tmp_path / "reading-list.md"
    path.write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Reading List\n\n"
        "Topic-specific inbox.\n\n"
        "## Processed\n",
        encoding="utf-8",
    )
    line = "- [ ] https://example.com/new"

    assert append_topic_reading_list(path, line, today=date(2026, 5, 28))

    text = path.read_text(encoding="utf-8")
    assert "modified: 2026-05-28" in text
    # New fallback heading is the canonical ## Pending (Phase-2 section ladder), not ## Queue.
    assert text.index("Topic-specific inbox.") < text.index("## Pending") < text.index(line)
    assert text.index(line) < text.index("## Processed")



def test_telegram_annotation_format() -> None:
    assert telegram_annotation("ai-literacy", date(2026, 5, 28)) == (
        "(via Telegram #ai-literacy, 2026-05-28)"
    )
