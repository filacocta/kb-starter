from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from kb_feed.dry_run import LIVE_KB_ROOT
from kb_feed.errors import KbFeedError
from kb_feed.locking import exclusive_kb_write_lock
from kb_feed.markdown import (
    append_dive_queue,
    append_review_queue,
    append_topic_auto_approved,
    append_topic_reading_list,
    telegram_annotation,
)
from kb_feed.state import CaptureRow, OutboxRow, StateStore, validate_topic_slug


LAST_COMMIT_META_KEY = "last_kb_commit_at"
DEFAULT_GIT_USER_NAME = "kb-bot"
DEFAULT_GIT_USER_EMAIL = "noreply@kb-feed.local"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    row_ids: tuple[int, ...] = ()
    capture_row_ids: tuple[int, ...] = ()
    commit_hash: str | None = None
    skipped_reason: str | None = None


@dataclass(frozen=True)
class FlushResult:
    written: tuple[int, ...]
    failed: tuple[int, ...]
    recovered: int
    capture_written: tuple[int, ...] = ()
    capture_failed: tuple[int, ...] = ()
    capture_recovered: int = 0
    commit: CommitResult | None = None


def flush_outbox(
    store: StateStore,
    kb_root: Path,
    *,
    allow_live_kb: bool = False,
    limit: int | None = None,
    today: date | None = None,
    lock_path: Path | None = None,
    commit: bool = False,
    force_commit: bool = False,
    commit_debounce_seconds: int = 300,
    commit_max_pending: int = 20,
    git_user_name: str = DEFAULT_GIT_USER_NAME,
    git_user_email: str = DEFAULT_GIT_USER_EMAIL,
    now: datetime | None = None,
) -> FlushResult:
    kb_root = kb_root.resolve()
    if kb_root == LIVE_KB_ROOT and not allow_live_kb:
        raise KbFeedError("Outbox flush refuses to write to the live KB root without --allow-live-kb.")

    with exclusive_kb_write_lock(kb_root, lock_path):
        captured_on = today or date.today()
        recovered = store.reset_writing_outbox()
        capture_recovered = store.reset_writing_captures()
        claim_limit = limit
        capture_claim_limit = limit
        if commit:
            pending_rows = store.list_outbox(status="pending")
            rows_to_claim = pending_rows if limit is None else pending_rows[:limit]
            pending_capture_rows = store.list_capture_outbox(status="pending")
            capture_rows_to_claim = pending_capture_rows if limit is None else pending_capture_rows[:limit]
            paths = _changed_paths_for_rows(rows_to_claim) + _changed_paths_for_capture_rows(
                capture_rows_to_claim
            )
            if paths:
                _assert_no_preexisting_git_changes(kb_root, sorted(set(paths)))
                claim_limit = len(rows_to_claim)
                capture_claim_limit = len(capture_rows_to_claim)

        rows = store.claim_pending_outbox(limit=claim_limit)
        capture_rows = store.claim_pending_captures(limit=capture_claim_limit)
        written: list[int] = []
        failed: list[int] = []
        capture_written: list[int] = []
        capture_failed: list[int] = []

        for row in rows:
            try:
                _write_outbox_row(row, kb_root, captured_on)
            except Exception as exc:  # Keep replay state recoverable even if one markdown write fails.
                store.mark_outbox_failed(row.id, str(exc))
                failed.append(row.id)
                continue

            store.mark_outbox_written(row.id)
            written.append(row.id)

        for row in capture_rows:
            try:
                _write_capture_row(row, kb_root, captured_on)
            except Exception as exc:
                store.mark_capture_failed(row.id, str(exc))
                capture_failed.append(row.id)
                continue

            store.mark_capture_written(row.id)
            capture_written.append(row.id)

        if commit and (written or capture_written):
            written_rows = tuple(row for row in rows if row.id in written)
            written_capture_rows = tuple(row for row in capture_rows if row.id in capture_written)
            _stage_paths(
                kb_root,
                sorted(
                    set(
                        _changed_paths_for_rows(written_rows)
                        + _changed_paths_for_capture_rows(written_capture_rows)
                    )
                ),
                git_user_name=git_user_name,
                git_user_email=git_user_email,
            )

        commit_result = None
        if commit:
            commit_result = _maybe_commit_written_items(
                store,
                kb_root,
                force=force_commit,
                debounce_seconds=commit_debounce_seconds,
                max_pending=commit_max_pending,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                now=now,
            )

        result = FlushResult(
            written=tuple(written),
            failed=tuple(failed),
            recovered=recovered,
            capture_written=tuple(capture_written),
            capture_failed=tuple(capture_failed),
            capture_recovered=capture_recovered,
            commit=commit_result,
        )

    if result.written or result.capture_written:
        _regenerate_dive_queue(kb_root)

    return result


def _regenerate_dive_queue(kb_root: Path) -> None:
    script_path = kb_root / "_tools" / "kb-derive-dive-queue.py"
    try:
        subprocess.run(
            [script_path, "--kb-root", kb_root, "--write"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Failed to regenerate KB dive queue after outbox flush: %s", exc)


def _write_outbox_row(row: OutboxRow, kb_root: Path, captured_on: date) -> None:
    write_outbox_row(row, kb_root, captured_on)


def write_outbox_row(row: OutboxRow, kb_root: Path, captured_on: date) -> None:
    topic_slug = validate_topic_slug(row.topic_slug)
    line = _url_queue_line(row, captured_on)
    if topic_slug == "unfiltered":
        append_review_queue(kb_root / "readinglist" / "Links.md", line, today=captured_on)
        return

    reading_list = kb_root / "topics" / topic_slug / "reading-list.md"
    if row.target_section == "auto-approved":
        append_topic_auto_approved(reading_list, line, today=captured_on)
        return
    append_topic_reading_list(reading_list, line, today=captured_on)


def _write_capture_row(row: CaptureRow, kb_root: Path, captured_on: date) -> None:
    if row.capture_type == "inspiration-capture":
        line = f"- [ ] {_single_line(row.text)} — {telegram_annotation(row.capture_type, captured_on)}"
        append_review_queue(kb_root / "readinglist" / "topics.md", line, today=captured_on)
        return
    if row.capture_type == "topic-idea":
        if row.topic_slug is None:
            raise KbFeedError("topic-idea capture is missing topic_slug.")
        topic_slug = validate_topic_slug(row.topic_slug)
        line = f"- [ ] {_single_line(row.text)} — {telegram_annotation(topic_slug, captured_on)}"
        append_dive_queue(kb_root / "_dive-queue.md", topic_slug, line, today=captured_on)
        return
    raise KbFeedError(f"Unsupported capture type: {row.capture_type}")


def _url_queue_line(row: OutboxRow, captured_on: date) -> str:
    annotation = telegram_annotation(row.topic_slug, captured_on)
    if row.note:
        return f"- [ ] {row.fetch_url} — {_single_line(row.note)} — {annotation}"
    return f"- [ ] {row.fetch_url} — {annotation}"


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _maybe_commit_written_items(
    store: StateStore,
    kb_root: Path,
    *,
    force: bool,
    debounce_seconds: int,
    max_pending: int,
    git_user_name: str,
    git_user_email: str,
    now: datetime | None,
) -> CommitResult:
    rows = store.list_uncommitted_written_outbox()
    capture_rows = store.list_uncommitted_written_captures()
    row_ids = tuple(row.id for row in rows)
    capture_row_ids = tuple(row.id for row in capture_rows)
    if not rows and not capture_rows:
        return CommitResult(committed=False, skipped_reason="no written rows awaiting commit")

    timestamp = now or datetime.now(timezone.utc)
    should_commit, reason = _commit_due(
        store,
        pending_count=len(rows) + len(capture_rows),
        force=force,
        debounce_seconds=debounce_seconds,
        max_pending=max_pending,
        now=timestamp,
    )
    if not should_commit:
        return CommitResult(
            committed=False,
            row_ids=row_ids,
            capture_row_ids=capture_row_ids,
            skipped_reason=reason,
        )

    paths = sorted(set(_changed_paths_for_rows(rows) + _changed_paths_for_capture_rows(capture_rows)))
    _assert_only_expected_staged_paths(kb_root, paths)
    if not _git_has_staged_changes(kb_root, paths):
        if _git_has_changes(kb_root, paths):
            return CommitResult(
                committed=False,
                row_ids=row_ids,
                capture_row_ids=capture_row_ids,
                skipped_reason="target files have unstaged changes; review before committing",
            )
        store.mark_outbox_committed(row_ids)
        store.mark_capture_committed(capture_row_ids)
        return CommitResult(
            committed=False,
            row_ids=row_ids,
            capture_row_ids=capture_row_ids,
            skipped_reason="no git changes to commit",
        )
    message = _commit_message(rows, capture_rows)
    _git(
        kb_root,
        ["commit", "-m", message[0], "-m", message[1]],
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    )
    commit_hash = _git(
        kb_root,
        ["rev-parse", "--short", "HEAD"],
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    ).stdout.strip()
    store.mark_outbox_committed(row_ids)
    store.mark_capture_committed(capture_row_ids)
    store.set_meta(LAST_COMMIT_META_KEY, timestamp.isoformat())
    return CommitResult(
        committed=True,
        row_ids=row_ids,
        capture_row_ids=capture_row_ids,
        commit_hash=commit_hash,
    )


def _commit_due(
    store: StateStore,
    *,
    pending_count: int,
    force: bool,
    debounce_seconds: int,
    max_pending: int,
    now: datetime,
) -> tuple[bool, str | None]:
    if force:
        return True, None
    if pending_count >= max(1, max_pending):
        return True, None
    if debounce_seconds <= 0:
        return True, None

    last_commit = store.get_meta(LAST_COMMIT_META_KEY)
    if last_commit is None:
        return True, None

    try:
        last_commit_at = datetime.fromisoformat(last_commit)
    except ValueError:
        return True, None
    if last_commit_at.tzinfo is None:
        last_commit_at = last_commit_at.replace(tzinfo=timezone.utc)
    if now - last_commit_at >= timedelta(seconds=debounce_seconds):
        return True, None

    return False, f"debounced; {pending_count} row(s) awaiting commit"


def _changed_paths_for_rows(rows: tuple[OutboxRow, ...]) -> list[str]:
    paths: set[str] = set()
    for row in rows:
        if row.topic_slug == "unfiltered":
            paths.add("readinglist/Links.md")
        else:
            paths.add(f"topics/{row.topic_slug}/reading-list.md")
    return sorted(paths)


def _changed_paths_for_capture_rows(rows: tuple[CaptureRow, ...]) -> list[str]:
    paths: set[str] = set()
    for row in rows:
        if row.capture_type == "inspiration-capture":
            paths.add("readinglist/topics.md")
        elif row.capture_type == "topic-idea":
            paths.add("_dive-queue.md")
    return sorted(paths)


def _assert_no_preexisting_git_changes(kb_root: Path, paths: list[str]) -> None:
    result = _git(
        kb_root,
        ["status", "--porcelain", "--", *paths],
        git_user_name=DEFAULT_GIT_USER_NAME,
        git_user_email=DEFAULT_GIT_USER_EMAIL,
    )
    dirty = result.stdout.strip()
    if dirty:
        raise KbFeedError(
            "Refusing to commit kb-feed changes because target KB queue files already have "
            f"uncommitted changes:\n{dirty}"
        )


def _stage_paths(
    kb_root: Path,
    paths: list[str],
    *,
    git_user_name: str,
    git_user_email: str,
) -> None:
    _git(
        kb_root,
        ["add", "--", *paths],
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    )


def _assert_only_expected_staged_paths(kb_root: Path, paths: list[str]) -> None:
    result = _git(
        kb_root,
        ["diff", "--cached", "--name-only"],
        git_user_name=DEFAULT_GIT_USER_NAME,
        git_user_email=DEFAULT_GIT_USER_EMAIL,
    )
    staged = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    unexpected = staged - set(paths)
    if unexpected:
        raise KbFeedError(
            "Refusing to commit kb-feed changes because unrelated paths are already staged:\n"
            + "\n".join(sorted(unexpected))
        )


def _git_has_staged_changes(kb_root: Path, paths: list[str]) -> bool:
    result = _git(
        kb_root,
        ["diff", "--cached", "--quiet", "--", *paths],
        git_user_name=DEFAULT_GIT_USER_NAME,
        git_user_email=DEFAULT_GIT_USER_EMAIL,
        allow_exit_codes={0, 1},
    )
    return result.returncode == 1


def _commit_message(rows: tuple[OutboxRow, ...], capture_rows: tuple[CaptureRow, ...]) -> tuple[str, str]:
    item_count = len(rows) + len(capture_rows)
    subject = f"kb-feed: capture {item_count} Telegram item(s)"
    body_lines = [
        "Telegram-origin queue entries written by kb-feed.",
        "",
    ]
    if rows:
        body_lines.append("URL outbox rows:")
        for row in rows:
            body_lines.append(
                f"- #{row.id} thread {row.thread_id} -> {row.topic_slug}: {row.fetch_url}"
            )
    if capture_rows:
        if rows:
            body_lines.append("")
        body_lines.append("Capture outbox rows:")
        for row in capture_rows:
            body_lines.append(f"- #{row.id} thread {row.thread_id} -> {row.capture_type}")
    return subject, "\n".join(body_lines)


def _git_has_changes(kb_root: Path, paths: list[str]) -> bool:
    result = _git(
        kb_root,
        ["status", "--porcelain", "--", *paths],
        git_user_name=DEFAULT_GIT_USER_NAME,
        git_user_email=DEFAULT_GIT_USER_EMAIL,
    )
    return bool(result.stdout.strip())


def _git(
    kb_root: Path,
    args: list[str],
    *,
    git_user_name: str,
    git_user_email: str,
    allow_exit_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={git_user_name}",
            "-c",
            f"user.email={git_user_email}",
            *args,
        ],
        cwd=kb_root,
        capture_output=True,
        text=True,
        check=False,
    )
    allowed = allow_exit_codes or {0}
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).strip()
        raise KbFeedError(f"Git command failed in {kb_root}: git {' '.join(args)}\n{detail}")
    return result
