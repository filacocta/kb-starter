from __future__ import annotations

import pytest

from kb_feed.errors import KbFeedError
from kb_feed.state import SCHEMA_VERSION, StateStore


def test_initialize_creates_schema(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")

    assert store.schema_version() == 0
    store.initialize()

    assert store.schema_version() == SCHEMA_VERSION


def test_initialize_creates_queue_undo_schema(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    with store.connect() as db:
        tables = {
            str(row["name"])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        outbox_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(outbox)").fetchall()}
        capture_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(capture_outbox)").fetchall()
        }
        seen_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(seen_urls)").fetchall()}

    assert "queue_batches" in tables
    assert "batch_id" in outbox_columns
    assert "target_section" in outbox_columns
    assert "batch_id" in capture_columns
    assert {"source", "batch_id"} <= seen_columns


def test_topic_map_crud(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    store.set_topic_mapping(123, "obsidian")
    store.set_topic_mapping(456, "ai-literacy")
    store.set_topic_mapping(123, "geospatial-mapping")

    assert store.topic_slug_for_thread(123) == "geospatial-mapping"
    assert store.topic_slug_for_thread(999) is None
    assert [(item.thread_id, item.topic_slug) for item in store.list_topic_mappings()] == [
        (123, "geospatial-mapping"),
        (456, "ai-literacy"),
    ]
    assert store.remove_topic_mapping(456)
    assert not store.remove_topic_mapping(456)


def test_topic_map_rejects_unsafe_slugs(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    with pytest.raises(KbFeedError):
        store.set_topic_mapping(123, "../obsidian")


def test_allowed_users(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    store.allow_user(111)
    store.allow_user(222)
    store.allow_user(111)

    assert store.is_user_allowed(111)
    assert not store.is_user_allowed(333)
    assert store.list_allowed_users() == (111, 222)
    assert store.revoke_user(222)
    assert store.list_allowed_users() == (111,)


def test_seen_urls_are_idempotent(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    assert store.mark_seen_url(
        "https://example.com/a",
        "https://example.com/a",
        thread_id=123,
        topic_slug="obsidian",
    )
    assert not store.mark_seen_url("https://example.com/a", "https://example.com/a")
    assert store.has_seen_url("https://example.com/a")


def test_queue_batch_reuses_redelivered_source_message(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    first = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    second = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)

    assert first == second
    assert store.get_queue_batch(first).source_message_id == 77


def test_outbox_enqueue_and_list(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://www.example.com/a?utm_source=x",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )

    rows = store.list_outbox()
    assert row_id == 1
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].target_section == "pending"
    assert rows[0].fetch_url == "https://example.com/a"
    assert rows[0].note is None
    assert rows[0].committed_at is None
    assert store.list_outbox(status="written") == ()


def test_outbox_can_target_auto_approved_section(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        target_section="auto-approved",
    )

    row = store.get_outbox(row_id)
    assert row is not None
    assert row.target_section == "auto-approved"


def test_outbox_preserves_optional_note(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        note="  useful queue example\nwith context  ",
    )

    assert store.list_outbox()[0].note == "useful queue example with context"


def test_enqueue_unseen_url_is_atomic_for_duplicates(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    first = store.enqueue_unseen_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://www.example.com/a?utm_source=x",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    second = store.enqueue_unseen_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )

    assert first.outbox_id == 1
    assert not first.is_duplicate
    assert second.outbox_id is None
    assert second.is_duplicate
    assert len(store.list_outbox()) == 1


def test_cancel_queue_batch_marks_pending_rows_canceled_and_releases_seen_url(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.enqueue_unseen_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        batch_id=batch_id,
    )

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.url_canceled == 1
    assert store.list_outbox()[0].status == "canceled"
    assert not store.has_seen_url("https://example.com/a")
    assert store.get_queue_batch(batch_id).canceled_at is not None


def test_cancel_queue_batch_preserves_bootstrap_seen_url(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    assert store.mark_seen_url("https://example.com/a", "https://example.com/a")
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        batch_id=batch_id,
    )

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.url_canceled == 1
    assert store.has_seen_url("https://example.com/a")


def test_cancel_queue_batch_does_not_release_seen_url_when_written_row_uses_it(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    written_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    store.mark_outbox_written(written_id)
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.mark_seen_url(
        "https://example.com/a",
        "https://example.com/a",
        source="telegram",
        batch_id=batch_id,
    )
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        batch_id=batch_id,
    )

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.url_canceled == 1
    assert store.has_seen_url("https://example.com/a")


def test_cancel_queue_batch_does_not_cancel_written_or_writing_rows(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    written_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/written",
        normalized_url="https://example.com/written",
        fetch_url="https://example.com/written",
        batch_id=batch_id,
    )
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/writing",
        normalized_url="https://example.com/writing",
        fetch_url="https://example.com/writing",
        batch_id=batch_id,
    )
    store.mark_outbox_written(written_id)
    store.claim_pending_outbox()

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.url_canceled == 0
    assert result.written == 1
    assert result.writing == 1
    assert [row.status for row in store.list_outbox()] == ["written", "writing"]


def test_cancel_queue_batch_repeated_written_attempt_still_reports_written(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/written",
        normalized_url="https://example.com/written",
        fetch_url="https://example.com/written",
        batch_id=batch_id,
    )
    store.mark_outbox_written(row_id)

    first = store.cancel_queue_batch(batch_id, actor_user_id=111)
    second = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert first.written == 1
    assert first.already_canceled == 0
    assert second.written == 1
    assert second.already_canceled == 0


def test_cancel_queue_batch_reports_partial_success(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    written_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/written",
        normalized_url="https://example.com/written",
        fetch_url="https://example.com/written",
        batch_id=batch_id,
    )
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/pending",
        normalized_url="https://example.com/pending",
        fetch_url="https://example.com/pending",
        batch_id=batch_id,
    )
    store.mark_outbox_written(written_id)

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.url_canceled == 1
    assert result.written == 1
    assert [row.status for row in store.list_outbox()] == ["written", "canceled"]


def test_cancel_queue_batch_is_idempotent(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    batch_id = store.create_queue_batch(chat_id=-100123, thread_id=123, source_message_id=77, source_user_id=111)
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
        batch_id=batch_id,
    )

    first = store.cancel_queue_batch(batch_id, actor_user_id=111)
    second = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert first.url_canceled == 1
    assert second.url_canceled == 0
    assert second.already_canceled == 1


def test_cancel_queue_batch_marks_capture_rows_canceled(tmp_path) -> None:
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

    result = store.cancel_queue_batch(batch_id, actor_user_id=111)

    assert result.capture_canceled == 1
    assert store.list_capture_outbox()[0].status == "canceled"


def test_claim_pending_and_reset_writing_outbox(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    for index in range(2):
        store.enqueue_url(
            thread_id=123,
            topic_slug="obsidian",
            original_url=f"https://example.com/{index}",
            normalized_url=f"https://example.com/{index}",
            fetch_url=f"https://example.com/{index}",
        )

    claimed = store.claim_pending_outbox(limit=1)

    assert [row.status for row in store.list_outbox()] == ["writing", "pending"]
    assert claimed[0].id == 1
    assert store.reset_writing_outbox() == 1
    assert [row.status for row in store.list_outbox()] == ["pending", "pending"]


def test_mark_outbox_written_and_failed(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )

    store.mark_outbox_failed(row_id, "boom")
    failed = store.list_outbox()[0]
    assert failed.status == "failed"
    assert failed.error == "boom"

    store.mark_outbox_written(row_id)
    written = store.list_outbox()[0]
    assert written.status == "written"
    assert written.error is None

    assert store.list_uncommitted_written_outbox()[0].id == row_id
    store.mark_outbox_committed((row_id,))
    assert store.list_uncommitted_written_outbox() == ()
    assert store.list_outbox()[0].committed_at is not None


def test_capture_outbox_lifecycle(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    row_id = store.enqueue_capture(
        thread_id=456,
        capture_type="inspiration-capture",
        text="  Look into analog field notebooks  ",
    )

    rows = store.list_capture_outbox()
    assert row_id == 1
    assert rows[0].capture_type == "inspiration-capture"
    assert rows[0].topic_slug is None
    assert rows[0].text == "Look into analog field notebooks"
    assert rows[0].status == "pending"
    assert rows[0].committed_at is None

    claimed = store.claim_pending_captures(limit=1)
    assert claimed[0].status == "writing"
    assert store.reset_writing_captures() == 1

    store.mark_capture_failed(row_id, "boom")
    failed = store.list_capture_outbox()[0]
    assert failed.status == "failed"
    assert failed.error == "boom"

    store.mark_capture_written(row_id)
    written = store.list_capture_outbox()[0]
    assert written.status == "written"
    assert written.error is None
    assert store.list_uncommitted_written_captures()[0].id == row_id

    store.mark_capture_committed((row_id,))
    assert store.list_uncommitted_written_captures() == ()
    assert store.list_capture_outbox()[0].committed_at is not None


def test_topic_idea_capture_requires_and_preserves_topic_slug(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    row_id = store.enqueue_capture(
        thread_id=123,
        capture_type="topic-idea",
        topic_slug="obsidian",
        text="Investigate Dataview dashboards.",
    )

    row = store.list_capture_outbox()[0]
    assert row.id == row_id
    assert row.capture_type == "topic-idea"
    assert row.topic_slug == "obsidian"

    with pytest.raises(KbFeedError, match="topic_slug is required"):
        store.enqueue_capture(thread_id=123, capture_type="topic-idea", text="missing topic")


def test_retry_failed_outbox(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    store.mark_outbox_failed(row_id, "boom")

    assert store.retry_failed_outbox((row_id,)) == 1

    row = store.list_outbox()[0]
    assert row.status == "pending"
    assert row.error is None


def test_runtime_meta_round_trips(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    assert store.get_meta("last_kb_commit_at") is None
    store.set_meta("last_kb_commit_at", "2026-05-31T12:00:00+00:00")

    assert store.get_meta("last_kb_commit_at") == "2026-05-31T12:00:00+00:00"


def test_state_methods_require_initialized_db(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")

    with pytest.raises(KbFeedError):
        store.set_topic_mapping(123, "obsidian")
