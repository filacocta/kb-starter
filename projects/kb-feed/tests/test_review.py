from __future__ import annotations

import fcntl
from datetime import date

import pytest

from kb_feed.errors import KbFeedError
from kb_feed.review import assign_link, promote_inspiration


def _build_kb(tmp_path):
    kb_root = tmp_path / "kb"
    (kb_root / "topics" / "rockhounding").mkdir(parents=True)
    (kb_root / "readinglist").mkdir(parents=True)
    (kb_root / "readinglist" / "Links.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# URL Inbox\n\n"
        "- [ ] https://example.com/mineral — (via Telegram #unfiltered, 2026-05-31)\n"
        "- [ ] https://example.com/other — manual save\n",
        encoding="utf-8",
    )
    (kb_root / "readinglist" / "topics.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Topic Interests\n\n"
        "- [ ] compare field kits with lab assays — (via Telegram #inspiration-capture, 2026-05-31)\n",
        encoding="utf-8",
    )
    (kb_root / "topics" / "rockhounding" / "reading-list.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Rockhounding Reading List\n\n"
        "## Queue\n",
        encoding="utf-8",
    )
    (kb_root / "_dive-queue.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Dive Queue\n\n"
        "## rockhounding\n",
        encoding="utf-8",
    )
    return kb_root


def test_assign_link_moves_unfiltered_url_to_topic_reading_list_and_marks_source(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)

    result = assign_link(
        kb_root,
        url="https://example.com/mineral",
        topic_slug="rockhounding",
        today=date(2026, 6, 1),
    )

    assert result.added_reading_list is True
    assert result.added_dive_queue is False
    reading_list = (kb_root / "topics" / "rockhounding" / "reading-list.md").read_text(encoding="utf-8")
    assert "- [ ] https://example.com/mineral — (via Telegram #unfiltered, 2026-05-31)" in reading_list
    inbox = (kb_root / "readinglist" / "Links.md").read_text(encoding="utf-8")
    assert "- [x] https://example.com/mineral — (via Telegram #unfiltered, 2026-05-31) — assigned to rockhounding 2026-06-01" in inbox


def test_assign_link_can_also_add_intentional_dive_queue_item(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)

    result = assign_link(
        kb_root,
        url="https://example.com/mineral",
        topic_slug="rockhounding",
        reason="investigate claim about mineral fluorescence",
        add_to_dive_queue=True,
        today=date(2026, 6, 1),
    )

    assert result.added_reading_list is True
    assert result.added_dive_queue is True
    dive_queue = (kb_root / "_dive-queue.md").read_text(encoding="utf-8")
    assert "- [ ] https://example.com/mineral — investigate claim about mineral fluorescence" in dive_queue


def test_promote_inspiration_adds_dive_queue_item_and_marks_source(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)

    result = promote_inspiration(
        kb_root,
        contains="field kits",
        topic_slug="rockhounding",
        idea="Compare field test kits against lab assays for amateur mineral identification",
        today=date(2026, 6, 1),
    )

    assert result.added_dive_queue is True
    dive_queue = (kb_root / "_dive-queue.md").read_text(encoding="utf-8")
    assert "- [ ] Compare field test kits against lab assays for amateur mineral identification" in dive_queue
    topics_inbox = (kb_root / "readinglist" / "topics.md").read_text(encoding="utf-8")
    assert "- [x] compare field kits with lab assays — (via Telegram #inspiration-capture, 2026-05-31) — promoted to rockhounding dive queue 2026-06-01" in topics_inbox


def test_assign_link_missing_url_raises(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)

    with pytest.raises(KbFeedError, match="No unchecked link found"):
        assign_link(kb_root, url="https://example.com/missing", topic_slug="rockhounding")


def test_assign_link_refuses_when_shared_write_lock_is_held(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    lock_path = kb_root / ".kb-write.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(KbFeedError, match="markdown writer"):
            assign_link(
                kb_root,
                url="https://example.com/mineral",
                topic_slug="rockhounding",
                lock_path=lock_path,
            )
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
