from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kb_feed.errors import KbFeedError


KB_WRITE_LOCK_NAME = ".kb-write.lock"


def kb_write_lock_path(kb_root: Path, lock_path: Path | None = None) -> Path:
    if lock_path is not None:
        return lock_path.expanduser()
    return kb_root.expanduser().resolve() / KB_WRITE_LOCK_NAME


@contextmanager
def exclusive_kb_write_lock(kb_root: Path, lock_path: Path | None = None) -> Iterator[Path]:
    path = kb_write_lock_path(kb_root, lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KbFeedError(f"Another KB markdown writer is already running: {path}") from exc
        try:
            yield path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
