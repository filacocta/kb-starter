from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from kb_feed.errors import KbFeedError


DEFAULT_STATE_DB = Path("~/.local/state/kb-feed/state.sqlite").expanduser()
SCHEMA_VERSION = 6
TOPIC_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
OUTBOX_TARGET_SECTIONS = {"pending", "auto-approved"}


@dataclass(frozen=True)
class TopicMapping:
    thread_id: int
    topic_slug: str


@dataclass(frozen=True)
class OutboxRow:
    id: int
    thread_id: int
    topic_slug: str
    original_url: str
    normalized_url: str
    fetch_url: str
    note: str | None
    status: str
    error: str | None
    committed_at: str | None = None
    batch_id: int | None = None
    target_section: str = "pending"


@dataclass(frozen=True)
class EnqueueResult:
    outbox_id: int | None
    is_duplicate: bool


@dataclass(frozen=True)
class CaptureRow:
    id: int
    thread_id: int
    capture_type: str
    topic_slug: str | None
    text: str
    status: str
    error: str | None
    committed_at: str | None = None
    batch_id: int | None = None


@dataclass(frozen=True)
class QueueBatch:
    id: int
    chat_id: int
    thread_id: int
    source_message_id: int
    confirmation_message_id: int | None
    source_user_id: int
    canceled_at: str | None = None


@dataclass(frozen=True)
class CancelBatchResult:
    batch_id: int | None
    url_canceled: int = 0
    capture_canceled: int = 0
    already_canceled: int = 0
    written: int = 0
    writing: int = 0
    failed: int = 0
    not_found: bool = False
    unauthorized: bool = False

    @property
    def canceled(self) -> int:
        return self.url_canceled + self.capture_canceled


class StateStore:
    def __init__(self, path: Path = DEFAULT_STATE_DB) -> None:
        self.path = path.expanduser()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            existing_version = int(db.execute("PRAGMA user_version").fetchone()[0])
            db.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS topic_map (
                    thread_id INTEGER PRIMARY KEY,
                    topic_slug TEXT NOT NULL CHECK (length(topic_slug) > 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS allowed_users (
                    user_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS seen_urls (
                    normalized_url TEXT PRIMARY KEY,
                    fetch_url TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    thread_id INTEGER,
                    topic_slug TEXT,
                    source TEXT NOT NULL DEFAULT 'bootstrap'
                        CHECK (source IN ('bootstrap', 'telegram')),
                    batch_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS queue_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    confirmation_message_id INTEGER,
                    source_user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    canceled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    topic_slug TEXT NOT NULL CHECK (length(topic_slug) > 0),
                    original_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    fetch_url TEXT NOT NULL,
                    note TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'writing', 'written', 'failed', 'canceled')),
                    error TEXT,
                    committed_at TEXT,
                    batch_id INTEGER REFERENCES queue_batches(id),
                    target_section TEXT NOT NULL DEFAULT 'pending'
                        CHECK (target_section IN ('pending', 'auto-approved')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS runtime_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS capture_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    capture_type TEXT NOT NULL CHECK (capture_type IN ('inspiration-capture', 'topic-idea')),
                    topic_slug TEXT,
                    text TEXT NOT NULL CHECK (length(text) > 0),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'writing', 'written', 'failed', 'canceled')),
                    error TEXT,
                    committed_at TEXT,
                    batch_id INTEGER REFERENCES queue_batches(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS outbox_status_idx ON outbox (status, id);
                CREATE INDEX IF NOT EXISTS seen_urls_topic_idx ON seen_urls (topic_slug);
                CREATE INDEX IF NOT EXISTS capture_outbox_status_idx ON capture_outbox (status, id);
                CREATE UNIQUE INDEX IF NOT EXISTS queue_batches_source_idx
                    ON queue_batches (chat_id, source_message_id);
                CREATE INDEX IF NOT EXISTS queue_batches_confirmation_idx
                    ON queue_batches (chat_id, thread_id, confirmation_message_id);
                """
            )
            if existing_version and existing_version < 4:
                _migrate_capture_outbox_v4(db)
            if existing_version and existing_version < 5:
                _migrate_outbox_v5(db)
                _migrate_capture_outbox_v5(db)
            _ensure_column(db, "outbox", "note", "TEXT")
            _ensure_column(db, "outbox", "committed_at", "TEXT")
            _ensure_column(db, "outbox", "batch_id", "INTEGER REFERENCES queue_batches(id)")
            _ensure_column(
                db,
                "outbox",
                "target_section",
                "TEXT NOT NULL DEFAULT 'pending' CHECK (target_section IN ('pending', 'auto-approved'))",
            )
            _ensure_column(db, "capture_outbox", "topic_slug", "TEXT")
            _ensure_column(db, "capture_outbox", "committed_at", "TEXT")
            _ensure_column(db, "capture_outbox", "batch_id", "INTEGER REFERENCES queue_batches(id)")
            _ensure_column(db, "seen_urls", "source", "TEXT NOT NULL DEFAULT 'bootstrap'")
            _ensure_column(db, "seen_urls", "batch_id", "INTEGER")
            db.execute(
                "UPDATE seen_urls SET source = 'bootstrap' WHERE source IS NULL OR source = ''"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with closing(self.connect()) as db:
            row = db.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def set_topic_mapping(self, thread_id: int, topic_slug: str) -> None:
        self._require_initialized()
        if thread_id <= 0:
            raise KbFeedError("thread_id must be a positive integer.")
        topic_slug = validate_topic_slug(topic_slug)

        with closing(self.connect()) as db, db:
            db.execute(
                """
                INSERT INTO topic_map (thread_id, topic_slug)
                VALUES (?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    topic_slug = excluded.topic_slug,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (thread_id, topic_slug),
            )

    def remove_topic_mapping(self, thread_id: int) -> bool:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            cursor = db.execute("DELETE FROM topic_map WHERE thread_id = ?", (thread_id,))
        return cursor.rowcount > 0

    def topic_slug_for_thread(self, thread_id: int) -> str | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT topic_slug FROM topic_map WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return str(row["topic_slug"]) if row else None

    def list_topic_mappings(self) -> tuple[TopicMapping, ...]:
        self._require_initialized()
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT thread_id, topic_slug FROM topic_map ORDER BY thread_id"
            ).fetchall()
        return tuple(TopicMapping(int(row["thread_id"]), str(row["topic_slug"])) for row in rows)

    def allow_user(self, user_id: int) -> None:
        self._require_initialized()
        if user_id <= 0:
            raise KbFeedError("user_id must be a positive integer.")
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)",
                (user_id,),
            )

    def revoke_user(self, user_id: int) -> bool:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            cursor = db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0

    def is_user_allowed(self, user_id: int) -> bool:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT 1 FROM allowed_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def list_allowed_users(self) -> tuple[int, ...]:
        self._require_initialized()
        with closing(self.connect()) as db:
            rows = db.execute("SELECT user_id FROM allowed_users ORDER BY user_id").fetchall()
        return tuple(int(row["user_id"]) for row in rows)

    def mark_seen_url(
        self,
        normalized_url: str,
        fetch_url: str,
        *,
        thread_id: int | None = None,
        topic_slug: str | None = None,
        source: str = "bootstrap",
        batch_id: int | None = None,
    ) -> bool:
        self._require_initialized()
        source = validate_seen_source(source)
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO seen_urls (normalized_url, fetch_url, thread_id, topic_slug, source, batch_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_url, fetch_url, thread_id, topic_slug, source, batch_id),
            )
        return cursor.rowcount > 0

    def has_seen_url(self, normalized_url: str) -> bool:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT 1 FROM seen_urls WHERE normalized_url = ?",
                (normalized_url,),
            ).fetchone()
        return row is not None

    def enqueue_url(
        self,
        *,
        thread_id: int,
        topic_slug: str,
        original_url: str,
        normalized_url: str,
        fetch_url: str,
        note: str | None = None,
        batch_id: int | None = None,
        target_section: str = "pending",
    ) -> int:
        self._require_initialized()
        topic_slug = validate_topic_slug(topic_slug)
        note = _clean_optional_text(note)
        target_section = validate_outbox_target_section(target_section)
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                INSERT INTO outbox (
                    thread_id, topic_slug, original_url, normalized_url, fetch_url, note, batch_id, target_section
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, topic_slug, original_url, normalized_url, fetch_url, note, batch_id, target_section),
            )
        return int(cursor.lastrowid)

    def enqueue_unseen_url(
        self,
        *,
        thread_id: int,
        topic_slug: str,
        original_url: str,
        normalized_url: str,
        fetch_url: str,
        note: str | None = None,
        batch_id: int | None = None,
        target_section: str = "pending",
    ) -> EnqueueResult:
        self._require_initialized()
        topic_slug = validate_topic_slug(topic_slug)
        note = _clean_optional_text(note)
        target_section = validate_outbox_target_section(target_section)
        source = "telegram" if batch_id is not None else "bootstrap"
        with closing(self.connect()) as db, db:
            seen_cursor = db.execute(
                """
                INSERT OR IGNORE INTO seen_urls (normalized_url, fetch_url, thread_id, topic_slug, source, batch_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_url, fetch_url, thread_id, topic_slug, source, batch_id),
            )
            if seen_cursor.rowcount == 0:
                return EnqueueResult(outbox_id=None, is_duplicate=True)

            outbox_cursor = db.execute(
                """
                INSERT INTO outbox (
                    thread_id, topic_slug, original_url, normalized_url, fetch_url, note, batch_id, target_section
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, topic_slug, original_url, normalized_url, fetch_url, note, batch_id, target_section),
            )
        return EnqueueResult(outbox_id=int(outbox_cursor.lastrowid), is_duplicate=False)

    def list_outbox(self, status: str | None = None) -> tuple[OutboxRow, ...]:
        self._require_initialized()
        query = (
            "SELECT id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note, status, error, committed_at, batch_id, target_section "
            "FROM outbox"
        )
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id"

        with closing(self.connect()) as db:
            rows = db.execute(query, params).fetchall()
        return _outbox_rows_from_sql(rows)

    def get_outbox(self, row_id: int) -> OutboxRow | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                """
                SELECT id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note,
                       status, error, committed_at, batch_id, target_section
                FROM outbox
                WHERE id = ?
                """,
                (row_id,),
            ).fetchone()
        return _outbox_rows_from_sql((row,))[0] if row is not None else None

    def reset_writing_outbox(self) -> int:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                UPDATE outbox
                SET status = 'pending',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'writing'
                """
            )
        return cursor.rowcount

    def claim_pending_outbox(self, *, limit: int | None = None) -> tuple[OutboxRow, ...]:
        self._require_initialized()
        limit_clause = "" if limit is None else " LIMIT ?"
        params: tuple[object, ...] = () if limit is None else (limit,)
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT id FROM outbox WHERE status = 'pending' ORDER BY id" + limit_clause,
                params,
            ).fetchall()
            row_ids = tuple(int(row["id"]) for row in rows)
            if not row_ids:
                return ()
            db.executemany(
                """
                UPDATE outbox
                SET status = 'writing',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                """,
                [(row_id,) for row_id in row_ids],
            )

            placeholders = ",".join("?" for _ in row_ids)
            claimed_rows = db.execute(
                """
                SELECT id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note,
                       status, error, committed_at, batch_id, target_section
                FROM outbox
                WHERE id IN (
                """
                + placeholders
                + ") AND status = 'writing' ORDER BY id",
                row_ids,
            ).fetchall()
        return _outbox_rows_from_sql(claimed_rows)

    def mark_outbox_written(self, row_id: int) -> None:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            db.execute(
                """
                UPDATE outbox
                SET status = 'written',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row_id,),
            )

    def list_uncommitted_written_outbox(self) -> tuple[OutboxRow, ...]:
        self._require_initialized()
        with closing(self.connect()) as db:
            rows = db.execute(
                """
                SELECT id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note,
                       status, error, committed_at, batch_id, target_section
                FROM outbox
                WHERE status = 'written'
                  AND committed_at IS NULL
                ORDER BY id
                """
            ).fetchall()
        return _outbox_rows_from_sql(rows)

    def mark_outbox_committed(self, row_ids: tuple[int, ...]) -> None:
        self._require_initialized()
        if not row_ids:
            return
        with closing(self.connect()) as db, db:
            db.executemany(
                """
                UPDATE outbox
                SET committed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                [(row_id,) for row_id in row_ids],
            )

    def mark_outbox_failed(self, row_id: int, error: str) -> None:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            db.execute(
                """
                UPDATE outbox
                SET status = 'failed',
                    error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error, row_id),
            )

    def retry_failed_outbox(self, row_ids: tuple[int, ...] = ()) -> int:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            if row_ids:
                cursor = db.executemany(
                    """
                    UPDATE outbox
                    SET status = 'pending',
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND status = 'failed'
                    """,
                    [(row_id,) for row_id in row_ids],
                )
                return cursor.rowcount

            cursor = db.execute(
                """
                UPDATE outbox
                SET status = 'pending',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'failed'
                """
            )
        return cursor.rowcount

    def enqueue_capture(
        self,
        *,
        thread_id: int,
        capture_type: str,
        text: str,
        topic_slug: str | None = None,
        batch_id: int | None = None,
    ) -> int:
        self._require_initialized()
        capture_type = validate_capture_type(capture_type)
        if capture_type == "topic-idea":
            if topic_slug is None:
                raise KbFeedError("topic_slug is required for topic-idea captures.")
            topic_slug = validate_topic_slug(topic_slug)
        elif topic_slug is not None:
            topic_slug = validate_topic_slug(topic_slug)
        text = text.strip()
        if not text:
            raise KbFeedError("capture text is required.")
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                INSERT INTO capture_outbox (thread_id, capture_type, topic_slug, text, batch_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, capture_type, topic_slug, text, batch_id),
            )
        return int(cursor.lastrowid)

    def list_capture_outbox(self, status: str | None = None) -> tuple[CaptureRow, ...]:
        self._require_initialized()
        query = "SELECT id, thread_id, capture_type, topic_slug, text, status, error, committed_at, batch_id FROM capture_outbox"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id"
        with closing(self.connect()) as db:
            rows = db.execute(query, params).fetchall()
        return _capture_rows_from_sql(rows)

    def reset_writing_captures(self) -> int:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                UPDATE capture_outbox
                SET status = 'pending',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'writing'
                """
            )
        return cursor.rowcount

    def claim_pending_captures(self, *, limit: int | None = None) -> tuple[CaptureRow, ...]:
        self._require_initialized()
        limit_clause = "" if limit is None else " LIMIT ?"
        params: tuple[object, ...] = () if limit is None else (limit,)
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT id FROM capture_outbox WHERE status = 'pending' ORDER BY id" + limit_clause,
                params,
            ).fetchall()
            row_ids = tuple(int(row["id"]) for row in rows)
            if not row_ids:
                return ()
            db.executemany(
                """
                UPDATE capture_outbox
                SET status = 'writing',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                """,
                [(row_id,) for row_id in row_ids],
            )
            placeholders = ",".join("?" for _ in row_ids)
            claimed_rows = db.execute(
                """
                SELECT id, thread_id, capture_type, topic_slug, text, status, error, committed_at, batch_id
                FROM capture_outbox
                WHERE id IN (
                """
                + placeholders
                + ") AND status = 'writing' ORDER BY id",
                row_ids,
            ).fetchall()
        return _capture_rows_from_sql(claimed_rows)

    def mark_capture_written(self, row_id: int) -> None:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            db.execute(
                """
                UPDATE capture_outbox
                SET status = 'written',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row_id,),
            )

    def mark_capture_failed(self, row_id: int, error: str) -> None:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            db.execute(
                """
                UPDATE capture_outbox
                SET status = 'failed',
                    error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error, row_id),
            )

    def list_uncommitted_written_captures(self) -> tuple[CaptureRow, ...]:
        self._require_initialized()
        with closing(self.connect()) as db:
            rows = db.execute(
                """
                SELECT id, thread_id, capture_type, topic_slug, text, status, error, committed_at, batch_id
                FROM capture_outbox
                WHERE status = 'written'
                  AND committed_at IS NULL
                ORDER BY id
                """
            ).fetchall()
        return _capture_rows_from_sql(rows)

    def mark_capture_committed(self, row_ids: tuple[int, ...]) -> None:
        self._require_initialized()
        if not row_ids:
            return
        with closing(self.connect()) as db, db:
            db.executemany(
                """
                UPDATE capture_outbox
                SET committed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                [(row_id,) for row_id in row_ids],
            )

    def create_queue_batch(
        self,
        *,
        chat_id: int,
        thread_id: int,
        source_message_id: int,
        source_user_id: int,
    ) -> int:
        self._require_initialized()
        if source_message_id <= 0:
            raise KbFeedError("source_message_id must be a positive integer.")
        if source_user_id <= 0:
            raise KbFeedError("source_user_id must be a positive integer.")
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """
                INSERT INTO queue_batches (chat_id, thread_id, source_message_id, source_user_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, source_message_id) DO UPDATE SET
                    thread_id = queue_batches.thread_id
                RETURNING id
                """,
                (chat_id, thread_id, source_message_id, source_user_id),
            )
            return int(cursor.fetchone()["id"])

    def attach_batch_confirmation(self, batch_id: int, confirmation_message_id: int) -> None:
        self._require_initialized()
        if confirmation_message_id <= 0:
            raise KbFeedError("confirmation_message_id must be a positive integer.")
        with closing(self.connect()) as db, db:
            db.execute(
                """
                UPDATE queue_batches
                SET confirmation_message_id = ?
                WHERE id = ?
                """,
                (confirmation_message_id, batch_id),
            )

    def get_queue_batch(self, batch_id: int) -> QueueBatch | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                """
                SELECT id, chat_id, thread_id, source_message_id, confirmation_message_id, source_user_id, canceled_at
                FROM queue_batches
                WHERE id = ?
                """,
                (batch_id,),
            ).fetchone()
        return _queue_batch_from_sql(row) if row is not None else None

    def find_batch_by_source_message(
        self,
        *,
        chat_id: int,
        thread_id: int,
        source_message_id: int,
    ) -> QueueBatch | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                """
                SELECT id, chat_id, thread_id, source_message_id, confirmation_message_id, source_user_id, canceled_at
                FROM queue_batches
                WHERE chat_id = ?
                  AND thread_id = ?
                  AND source_message_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, thread_id, source_message_id),
            ).fetchone()
        return _queue_batch_from_sql(row) if row is not None else None

    def find_batch_by_confirmation_message(
        self,
        *,
        chat_id: int,
        thread_id: int,
        confirmation_message_id: int,
    ) -> QueueBatch | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute(
                """
                SELECT id, chat_id, thread_id, source_message_id, confirmation_message_id, source_user_id, canceled_at
                FROM queue_batches
                WHERE chat_id = ?
                  AND thread_id = ?
                  AND confirmation_message_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, thread_id, confirmation_message_id),
            ).fetchone()
        return _queue_batch_from_sql(row) if row is not None else None

    def cancel_queue_batch(self, batch_id: int, *, actor_user_id: int) -> CancelBatchResult:
        self._require_initialized()
        if actor_user_id <= 0:
            return CancelBatchResult(batch_id=batch_id, unauthorized=True)
        with closing(self.connect()) as db:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            try:
                batch = db.execute(
                    """
                    SELECT id, source_user_id, canceled_at
                    FROM queue_batches
                    WHERE id = ?
                    """,
                    (batch_id,),
                ).fetchone()
                if batch is None:
                    db.execute("COMMIT")
                    return CancelBatchResult(batch_id=batch_id, not_found=True)
                if int(batch["source_user_id"]) != actor_user_id:
                    db.execute("COMMIT")
                    return CancelBatchResult(batch_id=batch_id, unauthorized=True)

                url_rows = db.execute(
                    "SELECT id, normalized_url, status FROM outbox WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
                capture_rows = db.execute(
                    "SELECT id, status FROM capture_outbox WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()

                url_cursor = db.execute(
                    """
                    UPDATE outbox
                    SET status = 'canceled',
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE batch_id = ?
                      AND status = 'pending'
                    """,
                    (batch_id,),
                )
                capture_cursor = db.execute(
                    """
                    UPDATE capture_outbox
                    SET status = 'canceled',
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE batch_id = ?
                      AND status = 'pending'
                    """,
                    (batch_id,),
                )

                canceled_urls = tuple(
                    str(row["normalized_url"]) for row in url_rows if str(row["status"]) == "pending"
                )
                for normalized_url in canceled_urls:
                    db.execute(
                        """
                        DELETE FROM seen_urls
                        WHERE normalized_url = ?
                          AND source = 'telegram'
                          AND batch_id = ?
                          AND NOT EXISTS (
                              SELECT 1
                              FROM outbox
                              WHERE normalized_url = ?
                                AND status != 'canceled'
                          )
                        """,
                        (normalized_url, batch_id, normalized_url),
                    )

                remaining_pending = db.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM outbox WHERE batch_id = ? AND status = 'pending') +
                        (SELECT COUNT(*) FROM capture_outbox WHERE batch_id = ? AND status = 'pending')
                    """,
                    (batch_id, batch_id),
                ).fetchone()[0]
                if int(remaining_pending) == 0:
                    db.execute(
                        """
                        UPDATE queue_batches
                        SET canceled_at = COALESCE(canceled_at, CURRENT_TIMESTAMP)
                        WHERE id = ?
                        """,
                        (batch_id,),
                    )

                all_statuses = [str(row["status"]) for row in url_rows] + [
                    str(row["status"]) for row in capture_rows
                ]
                already_canceled = sum(1 for status in all_statuses if status == "canceled")
                written = sum(1 for status in all_statuses if status == "written")
                writing = sum(1 for status in all_statuses if status == "writing")
                failed = sum(1 for status in all_statuses if status == "failed")
                if (
                    batch["canceled_at"] is not None
                    and not url_cursor.rowcount
                    and not capture_cursor.rowcount
                    and all_statuses
                    and all(status == "canceled" for status in all_statuses)
                ):
                    already_canceled = len(all_statuses)

                db.execute("COMMIT")
                return CancelBatchResult(
                    batch_id=batch_id,
                    url_canceled=int(url_cursor.rowcount),
                    capture_canceled=int(capture_cursor.rowcount),
                    already_canceled=already_canceled,
                    written=written,
                    writing=writing,
                    failed=failed,
                    not_found=not all_statuses,
                )
            except Exception:
                db.execute("ROLLBACK")
                raise

    def touch_activity(self, timestamp: str | None = None) -> None:
        from datetime import datetime, timezone

        value = timestamp or datetime.now(timezone.utc).isoformat()
        self.set_meta("last_activity_at", value)

    def get_meta(self, key: str) -> str | None:
        self._require_initialized()
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM runtime_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._require_initialized()
        with closing(self.connect()) as db, db:
            db.execute(
                """
                INSERT INTO runtime_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def _require_initialized(self) -> None:
        if self.schema_version() != SCHEMA_VERSION:
            raise KbFeedError(f"State DB is not initialized: {self.path}")


def _outbox_rows_from_sql(rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...]) -> tuple[OutboxRow, ...]:
    return tuple(
        OutboxRow(
            id=int(row["id"]),
            thread_id=int(row["thread_id"]),
            topic_slug=str(row["topic_slug"]),
            original_url=str(row["original_url"]),
            normalized_url=str(row["normalized_url"]),
            fetch_url=str(row["fetch_url"]),
            note=str(row["note"]) if "note" in row.keys() and row["note"] is not None else None,
            status=str(row["status"]),
            error=str(row["error"]) if row["error"] is not None else None,
            committed_at=str(row["committed_at"]) if row["committed_at"] is not None else None,
            batch_id=int(row["batch_id"]) if "batch_id" in row.keys() and row["batch_id"] is not None else None,
            target_section=(
                str(row["target_section"]) if "target_section" in row.keys() and row["target_section"] is not None else "pending"
            ),
        )
        for row in rows
    )


def _capture_rows_from_sql(rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...]) -> tuple[CaptureRow, ...]:
    return tuple(
        CaptureRow(
            id=int(row["id"]),
            thread_id=int(row["thread_id"]),
            capture_type=str(row["capture_type"]),
            topic_slug=str(row["topic_slug"]) if "topic_slug" in row.keys() and row["topic_slug"] is not None else None,
            text=str(row["text"]),
            status=str(row["status"]),
            error=str(row["error"]) if row["error"] is not None else None,
            committed_at=str(row["committed_at"]) if row["committed_at"] is not None else None,
            batch_id=int(row["batch_id"]) if "batch_id" in row.keys() and row["batch_id"] is not None else None,
        )
        for row in rows
    )


def _queue_batch_from_sql(row: sqlite3.Row) -> QueueBatch:
    return QueueBatch(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        thread_id=int(row["thread_id"]),
        source_message_id=int(row["source_message_id"]),
        confirmation_message_id=(
            int(row["confirmation_message_id"]) if row["confirmation_message_id"] is not None else None
        ),
        source_user_id=int(row["source_user_id"]),
        canceled_at=str(row["canceled_at"]) if row["canceled_at"] is not None else None,
    )


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_outbox_v5(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outbox'").fetchone()
    if row is None:
        return
    sql = str(row["sql"])
    if "canceled" in sql and "batch_id" in sql:
        return
    columns = {str(info["name"]) for info in db.execute("PRAGMA table_info(outbox)").fetchall()}
    note_expr = "note" if "note" in columns else "NULL"
    committed_at_expr = "committed_at" if "committed_at" in columns else "NULL"
    batch_id_expr = "batch_id" if "batch_id" in columns else "NULL"
    db.execute("DROP INDEX IF EXISTS outbox_status_idx")
    db.execute(
        """
        CREATE TABLE outbox_v5 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            topic_slug TEXT NOT NULL CHECK (length(topic_slug) > 0),
            original_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            fetch_url TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'writing', 'written', 'failed', 'canceled')),
            error TEXT,
            committed_at TEXT,
            batch_id INTEGER REFERENCES queue_batches(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        INSERT INTO outbox_v5 (
            id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note,
            status, error, committed_at, batch_id, created_at, updated_at
        )
        SELECT
            id, thread_id, topic_slug, original_url, normalized_url, fetch_url, {note_expr},
            status, error, {committed_at_expr}, {batch_id_expr}, created_at, updated_at
        FROM outbox
        """.format(note_expr=note_expr, committed_at_expr=committed_at_expr, batch_id_expr=batch_id_expr)
    )
    db.execute("DROP TABLE outbox")
    db.execute("ALTER TABLE outbox_v5 RENAME TO outbox")
    db.execute("CREATE INDEX outbox_status_idx ON outbox (status, id)")


def _migrate_capture_outbox_v5(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'capture_outbox'"
    ).fetchone()
    if row is None:
        return
    sql = str(row["sql"])
    if "canceled" in sql and "batch_id" in sql:
        return
    columns = {str(info["name"]) for info in db.execute("PRAGMA table_info(capture_outbox)").fetchall()}
    topic_slug_expr = "topic_slug" if "topic_slug" in columns else "NULL"
    committed_at_expr = "committed_at" if "committed_at" in columns else "NULL"
    batch_id_expr = "batch_id" if "batch_id" in columns else "NULL"
    db.execute("DROP INDEX IF EXISTS capture_outbox_status_idx")
    db.execute(
        """
        CREATE TABLE capture_outbox_v5 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            capture_type TEXT NOT NULL CHECK (capture_type IN ('inspiration-capture', 'topic-idea')),
            topic_slug TEXT,
            text TEXT NOT NULL CHECK (length(text) > 0),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'writing', 'written', 'failed', 'canceled')),
            error TEXT,
            committed_at TEXT,
            batch_id INTEGER REFERENCES queue_batches(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        INSERT INTO capture_outbox_v5 (
            id, thread_id, capture_type, topic_slug, text, status, error,
            committed_at, batch_id, created_at, updated_at
        )
        SELECT
            id, thread_id, capture_type, {topic_slug_expr}, text, status, error,
            {committed_at_expr}, {batch_id_expr}, created_at, updated_at
        FROM capture_outbox
        """.format(
            topic_slug_expr=topic_slug_expr,
            committed_at_expr=committed_at_expr,
            batch_id_expr=batch_id_expr,
        )
    )
    db.execute("DROP TABLE capture_outbox")
    db.execute("ALTER TABLE capture_outbox_v5 RENAME TO capture_outbox")
    db.execute("CREATE INDEX capture_outbox_status_idx ON capture_outbox (status, id)")


def _migrate_capture_outbox_v4(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'capture_outbox'"
    ).fetchone()
    if row is None or "topic-idea" in str(row["sql"]):
        return
    columns = {str(info["name"]) for info in db.execute("PRAGMA table_info(capture_outbox)").fetchall()}
    committed_at_expr = "committed_at" if "committed_at" in columns else "NULL"
    db.execute("DROP INDEX IF EXISTS capture_outbox_status_idx")
    db.execute(
        """
        CREATE TABLE capture_outbox_v4 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            capture_type TEXT NOT NULL CHECK (capture_type IN ('inspiration-capture', 'topic-idea')),
            topic_slug TEXT,
            text TEXT NOT NULL CHECK (length(text) > 0),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'writing', 'written', 'failed')),
            error TEXT,
            committed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        INSERT INTO capture_outbox_v4 (
            id, thread_id, capture_type, topic_slug, text, status, error, committed_at, created_at, updated_at
        )
        SELECT
            id, thread_id, capture_type, NULL, text, status, error, {committed_at_expr}, created_at, updated_at
        FROM capture_outbox
        """.format(committed_at_expr=committed_at_expr)
    )
    db.execute("DROP TABLE capture_outbox")
    db.execute("ALTER TABLE capture_outbox_v4 RENAME TO capture_outbox")
    db.execute("CREATE INDEX capture_outbox_status_idx ON capture_outbox (status, id)")


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def validate_topic_slug(topic_slug: str) -> str:
    value = topic_slug.strip()
    if not value:
        raise KbFeedError("topic_slug is required.")
    if not TOPIC_SLUG_RE.fullmatch(value):
        raise KbFeedError(
            "topic_slug must use lowercase letters, numbers, and hyphens, "
            "start with a letter or number, and be at most 63 characters."
        )
    return value


def validate_capture_type(capture_type: str) -> str:
    value = capture_type.strip()
    if value not in {"inspiration-capture", "topic-idea"}:
        raise KbFeedError("capture_type must be inspiration-capture or topic-idea.")
    return value


def validate_outbox_target_section(target_section: str) -> str:
    value = target_section.strip()
    if value not in OUTBOX_TARGET_SECTIONS:
        raise KbFeedError("outbox target_section must be pending or auto-approved.")
    return value


def validate_seen_source(source: str) -> str:
    value = source.strip()
    if value not in {"bootstrap", "telegram"}:
        raise KbFeedError("seen URL source must be bootstrap or telegram.")
    return value
