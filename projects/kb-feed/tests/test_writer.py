from __future__ import annotations

import fcntl
import subprocess
from datetime import date
from datetime import datetime
from datetime import timezone

import pytest

from kb_feed.dry_run import LIVE_KB_ROOT
from kb_feed.errors import KbFeedError
from kb_feed.state import StateStore
from kb_feed.writer import flush_outbox


def _kb_fixture(tmp_path, *, include_dive_heading: bool = True):
    kb_root = tmp_path / "kb"
    topic_dir = kb_root / "topics" / "obsidian"
    topic_dir.mkdir(parents=True)
    readinglist_dir = kb_root / "readinglist"
    readinglist_dir.mkdir(parents=True)
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
    dive_text = (
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Dive Queue\n\n"
    )
    if include_dive_heading:
        dive_text += "## obsidian\n"
    (kb_root / "_dive-queue.md").write_text(dive_text, encoding="utf-8")
    for filename, title in (("Links.md", "Links"), ("topics.md", "Topics")):
        (readinglist_dir / filename).write_text(
            "---\n"
            "author: kb-bot\n"
            "modified: 2026-05-01\n"
            "type: meta\n"
            "---\n\n"
            f"# {title}\n",
            encoding="utf-8",
        )
    return kb_root


def _store_with_outbox(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://www.example.com/a?utm_source=x",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    return store


def test_flush_outbox_writes_markdown_and_marks_written(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = "- [ ] https://example.com/a — (via Telegram #obsidian, 2026-05-31)"
    assert result.written == (1,)
    assert result.failed == ()
    assert store.list_outbox()[0].status == "written"
    assert line in (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_flush_outbox_regenerates_dive_queue_after_writes(tmp_path, monkeypatch) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        lock_path = kb_root / ".kb-write.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("kb_feed.writer.subprocess.run", fake_run)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.written == (1,)
    assert calls == [
        (
            [kb_root / "_tools" / "kb-derive-dive-queue.py", "--kb-root", kb_root, "--write"],
            {"capture_output": True, "text": True, "check": True, "timeout": 30},
        )
    ]


def test_flush_outbox_does_not_regenerate_dive_queue_without_writes(tmp_path, monkeypatch) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    calls = []

    monkeypatch.setattr("kb_feed.writer.subprocess.run", lambda *args, **kwargs: calls.append(args))

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.written == ()
    assert result.capture_written == ()
    assert calls == []


def test_flush_outbox_dive_queue_regeneration_failure_does_not_raise(
    tmp_path, monkeypatch, caplog
) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr="derive failed")

    monkeypatch.setattr("kb_feed.writer.subprocess.run", fake_run)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.written == (1,)
    assert "Failed to regenerate KB dive queue" in caplog.text


def test_flush_outbox_recovers_interrupted_writing_rows(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)
    store.claim_pending_outbox()

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.recovered == 1
    assert result.written == (1,)
    assert store.list_outbox()[0].status == "written"


def test_flush_outbox_creates_missing_topic_queue(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    (kb_root / "topics" / "obsidian" / "reading-list.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Obsidian Reading List\n",
        encoding="utf-8",
    )
    store = _store_with_outbox(tmp_path)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = "- [ ] https://example.com/a — (via Telegram #obsidian, 2026-05-31)"
    row = store.list_outbox()[0]
    text = (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert result.written == (1,)
    assert result.failed == ()
    assert row.status == "written"
    assert text.index("## Queue") < text.index(line)


def test_flush_outbox_skips_canceled_rows(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/canceled",
        normalized_url="https://example.com/canceled",
        fetch_url="https://example.com/canceled",
        batch_id=batch_id,
    )
    store.cancel_queue_batch(batch_id, actor_user_id=111)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.written == ()
    assert "https://example.com/canceled" not in (
        kb_root / "topics" / "obsidian" / "reading-list.md"
    ).read_text(encoding="utf-8")


def test_flush_outbox_skips_canceled_captures(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.enqueue_capture(
        thread_id=123,
        capture_type="topic-idea",
        topic_slug="obsidian",
        text="Investigate undoable queues.",
        batch_id=batch_id,
    )
    store.cancel_queue_batch(batch_id, actor_user_id=111)

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    assert result.capture_written == ()
    assert "Investigate undoable queues." not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_flush_outbox_preserves_url_note_in_reading_list(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        note="good overview of dataview use",
    )

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = (
        "- [ ] https://example.com/a — good overview of dataview use "
        "— (via Telegram #obsidian, 2026-05-31)"
    )
    assert result.written == (1,)
    assert line in (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_flush_outbox_writes_unfiltered_links_to_review_queue(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_url(
        thread_id=456,
        topic_slug="unfiltered",
        original_url="https://example.com/unclear",
        normalized_url="https://example.com/unclear",
        fetch_url="https://example.com/unclear",
    )

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = "- [ ] https://example.com/unclear — (via Telegram #unfiltered, 2026-05-31)"
    assert result.written == (1,)
    assert line in (kb_root / "readinglist" / "Links.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_flush_outbox_writes_inspiration_capture_to_topics_review_queue(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_capture(
        thread_id=789,
        capture_type="inspiration-capture",
        text="Check if public lidar can guide\nrockhounding trips.",
    )

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = (
        "- [ ] Check if public lidar can guide rockhounding trips. "
        "— (via Telegram #inspiration-capture, 2026-05-31)"
    )
    assert result.written == ()
    assert result.capture_written == (1,)
    assert line in (kb_root / "readinglist" / "topics.md").read_text(encoding="utf-8")
    assert store.list_capture_outbox()[0].status == "written"


def test_flush_outbox_writes_topic_idea_to_dive_queue(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_capture(
        thread_id=123,
        capture_type="topic-idea",
        topic_slug="obsidian",
        text="Investigate whether Dataview can power a queue dashboard.",
    )

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    line = (
        "- [ ] Investigate whether Dataview can power a queue dashboard. "
        "— (via Telegram #obsidian, 2026-05-31)"
    )
    assert result.capture_written == (1,)
    assert line in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "readinglist" / "topics.md").read_text(encoding="utf-8")


def test_flush_outbox_refuses_live_kb_without_flag(tmp_path) -> None:
    store = _store_with_outbox(tmp_path)

    with pytest.raises(KbFeedError):
        flush_outbox(store, LIVE_KB_ROOT)


def test_flush_outbox_refuses_when_writer_lock_is_held(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)
    lock_path = tmp_path / "kb-writer.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(KbFeedError, match="already running"):
            flush_outbox(store, kb_root, lock_path=lock_path)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def test_flush_outbox_defaults_to_kb_root_write_lock(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = _store_with_outbox(tmp_path)
    lock_path = kb_root / ".kb-write.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(KbFeedError, match="markdown writer"):
            flush_outbox(store, kb_root)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    assert store.list_outbox()[0].status == "pending"


def test_flush_outbox_writes_auto_approved_target_and_preserves_existing_lines(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    reading_list = kb_root / "topics" / "obsidian" / "reading-list.md"
    reading_list.write_text(
        reading_list.read_text(encoding="utf-8") + "- [ ] https://manual.example/already-there\n",
        encoding="utf-8",
    )
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/auto",
        normalized_url="https://example.com/auto",
        fetch_url="https://example.com/auto",
        target_section="auto-approved",
    )

    result = flush_outbox(store, kb_root, today=date(2026, 5, 31))

    text = reading_list.read_text(encoding="utf-8")
    auto_line = "- [ ] https://example.com/auto — (via Telegram #obsidian, 2026-05-31)"
    assert result.written == (1,)
    assert text.index("## Auto-approved") < text.index(auto_line) < text.index("## Queue")
    assert "- [ ] https://manual.example/already-there" in text


def test_flush_outbox_force_commits_written_rows_with_kb_bot_identity(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    _init_git_baseline(kb_root)
    store = _store_with_outbox(tmp_path)

    result = flush_outbox(
        store,
        kb_root,
        today=date(2026, 5, 31),
        commit=True,
        force_commit=True,
        git_user_name="kb-bot",
        git_user_email="noreply@kb-feed.local",
        now=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert result.commit is not None
    assert result.commit.committed
    assert result.commit.row_ids == (1,)
    assert store.list_outbox()[0].committed_at is not None
    log = _git(kb_root, "log", "-1", "--pretty=%an <%ae>%n%B").stdout
    assert "kb-bot <noreply@kb-feed.local>" in log
    assert "kb-feed: capture 1 Telegram item(s)" in log
    assert "#1 thread 123 -> obsidian: https://example.com/a" in log


def test_flush_outbox_force_commits_inspiration_capture(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    _init_git_baseline(kb_root)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.enqueue_capture(
        thread_id=789,
        capture_type="inspiration-capture",
        text="Research field note workflows.",
    )

    result = flush_outbox(
        store,
        kb_root,
        today=date(2026, 5, 31),
        commit=True,
        force_commit=True,
        now=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert result.commit is not None
    assert result.commit.committed
    assert result.commit.row_ids == ()
    assert result.commit.capture_row_ids == (1,)
    assert store.list_capture_outbox()[0].committed_at is not None
    log = _git(kb_root, "log", "-1", "--pretty=%B").stdout
    assert "kb-feed: capture 1 Telegram item(s)" in log
    assert "#1 thread 789 -> inspiration-capture" in log


def test_flush_outbox_debounces_recent_commits(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    _init_git_baseline(kb_root)
    store = _store_with_outbox(tmp_path)
    first_now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    flush_outbox(
        store,
        kb_root,
        today=date(2026, 5, 31),
        commit=True,
        force_commit=True,
        now=first_now,
    )
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/b",
        normalized_url="https://example.com/b",
        fetch_url="https://example.com/b",
    )

    result = flush_outbox(
        store,
        kb_root,
        today=date(2026, 5, 31),
        commit=True,
        commit_debounce_seconds=300,
        commit_max_pending=20,
        now=datetime(2026, 5, 31, 12, 1, tzinfo=timezone.utc),
    )

    assert result.commit is not None
    assert not result.commit.committed
    assert result.commit.row_ids == (2,)
    assert "debounced" in (result.commit.skipped_reason or "")
    assert store.list_outbox()[1].committed_at is None


def test_flush_outbox_commit_refuses_preexisting_dirty_queue_file(tmp_path) -> None:
    kb_root = _kb_fixture(tmp_path)
    _init_git_baseline(kb_root)
    store = _store_with_outbox(tmp_path)
    reading_list = kb_root / "topics" / "obsidian" / "reading-list.md"
    reading_list.write_text(
        reading_list.read_text(encoding="utf-8") + "- [ ] https://manual.example/a\n",
        encoding="utf-8",
    )

    with pytest.raises(KbFeedError, match="already have uncommitted changes"):
        flush_outbox(store, kb_root, commit=True, force_commit=True)

    assert store.list_outbox()[0].status == "pending"


def _init_git_baseline(kb_root) -> None:
    _git(kb_root, "init")
    _git(kb_root, "add", ".")
    _git(kb_root, "commit", "-m", "baseline")


def _git(cwd, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=kb-bot",
            "-c",
            "user.email=noreply@kb-feed.local",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result
