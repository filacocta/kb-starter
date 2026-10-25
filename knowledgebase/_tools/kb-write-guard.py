#!/usr/bin/env python3
"""Shared filesystem guards for KB writers.

The four queue/log writers use this module through a small, importlib-loaded
surface because the tool filenames contain hyphens::

    with write_lock(kb_root, lock_path, nonblocking=False):
        text = guarded_text(queue_path, kb_root)
        atomic_write(queue_path, updated_text)

Public helpers:

``canonical_root(path)``
    Return a real, existing KB directory.  Symlink components and ``..``
    components are rejected.
``write_lock(root, lock_path=None, nonblocking=False)``
    Open/create exactly ``root/.kb-write.lock`` with ``O_NOFOLLOW`` and hold an
    exclusive flock.  The descriptor must be a singly-linked regular file.
``canonical_queue_path(path, root)``
    Validate one of the sanctioned queue/backlog paths without following any
    symlink.
``guarded_text(path, root)``
    Read a canonical queue through a checked regular-file descriptor and
    require frontmatter ``author: kb-bot``.
``regular_text(path)``
    Read any existing regular, singly-linked, non-symlink file safely.  This is
    used for the activity log after its caller has established the exact path.
``atomic_write(path, text, prefix='.kb-write-')``
    Write a unique same-directory temporary file, preserve the target mode,
    fsync it, and replace the original only while its checked inode is still
    present.

No helper mutates a path before its checks pass.  In particular, lock
acquisition uses ``os.open(..., O_CREAT | O_NOFOLLOW)`` rather than ``touch``.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import tempfile
from pathlib import Path

import yaml


LOCK_NAME = ".kb-write.lock"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", None)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate frontmatter keys."""

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class WriteGuardError(ValueError):
    """A path, descriptor, ownership, or lock contract was unsafe."""


class LockBusyError(WriteGuardError):
    """A non-blocking lock acquisition found another writer."""


def _absolute(path: Path | str, base: Path | None = None) -> Path:
    """Make a lexical absolute path without normalising away ``..``."""
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = (base if base is not None else Path.cwd()) / value
    if ".." in value.parts:
        raise WriteGuardError("parent traversal is not allowed")
    return value


def _walk_no_symlinks(path: Path, *, allow_final_missing: bool = False) -> None:
    """Reject symlink/non-directory path components using lstat.

    This lexical walk complements ``O_NOFOLLOW`` on the final open.  The open
    remains authoritative for the descriptor and closes the usual check/open
    gap for the final component.
    """
    if not path.is_absolute():
        raise WriteGuardError("path must be absolute")
    parts = path.parts
    cursor = Path(path.anchor)
    for index, part in enumerate(parts):
        if index == 0 and part == path.anchor:
            continue
        cursor /= part
        final = index == len(parts) - 1
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if final and allow_final_missing:
                return
            raise
        if stat.S_ISLNK(info.st_mode):
            raise WriteGuardError(f"symlink path component is not allowed: {cursor}")
        if not final and not stat.S_ISDIR(info.st_mode):
            raise WriteGuardError(f"path component is not a directory: {cursor}")


def canonical_root(kb_root: Path | str) -> Path:
    """Return an existing, non-symlink KB root directory."""
    requested = _absolute(kb_root)
    resolved = requested.resolve(strict=True)
    # Equality is intentional: it rejects a root reached through a symlink as
    # well as an explicit parent traversal, instead of silently re-rooting it.
    if requested != resolved:
        raise WriteGuardError("KB root must not contain symlinks or parent traversal")
    _walk_no_symlinks(requested)
    info = os.stat(requested, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise WriteGuardError("KB root must be a directory")
    return requested


def _relative_to_root(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise WriteGuardError("path escapes the KB root") from exc


def canonical_queue_path(path: Path | str, kb_root: Path | str) -> Path:
    """Validate and return a canonical mutable queue/backlog path."""
    root = canonical_root(kb_root)
    candidate = _absolute(path, root)
    relative = _relative_to_root(candidate, root)
    allowed = {
        "readinglist/Links.md",
        "readinglist/topics.md",
        "ideas/_backlog.md",
    }
    is_topic_list = (
        len(relative.parts) == 3
        and relative.parts[0] == "topics"
        and relative.parts[2] == "reading-list.md"
    )
    if relative.as_posix() not in allowed and not is_topic_list:
        raise WriteGuardError("target is not a canonical queue or ideas backlog")
    _walk_no_symlinks(candidate)
    return candidate


def _open_regular(path: Path | str, *, flags: int, mode: int = 0o600,
                  allow_final_missing: bool = False) -> tuple[int, os.stat_result]:
    """Open a regular singly-linked file with a no-follow final descriptor."""
    if _O_NOFOLLOW is None:
        raise WriteGuardError("platform lacks O_NOFOLLOW; refusing KB write")
    candidate = _absolute(path)
    _walk_no_symlinks(candidate, allow_final_missing=allow_final_missing)
    open_flags = flags | _O_NOFOLLOW | _O_CLOEXEC
    try:
        descriptor = os.open(candidate, open_flags, mode)
    except OSError:
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WriteGuardError(f"file must be a regular file: {candidate}")
        if info.st_nlink != 1:
            raise WriteGuardError(f"multiply-linked file is not allowed: {candidate}")
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def regular_text(path: Path | str) -> str:
    """Read a checked regular file without following a symlink."""
    candidate = _absolute(path)
    descriptor, _ = _open_regular(
        candidate,
        flags=os.O_RDONLY | _O_NONBLOCK,
    )
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _frontmatter(text: str, path: Path) -> dict:
    if not text.startswith("---\n"):
        raise WriteGuardError(f"queue target lacks YAML frontmatter: {path}")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise WriteGuardError(f"queue target lacks YAML frontmatter: {path}")
    try:
        metadata = yaml.load(text[4:marker], Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise WriteGuardError(f"queue target has invalid YAML frontmatter: {path}") from exc
    if not isinstance(metadata, dict):
        raise WriteGuardError(f"queue target has invalid YAML frontmatter: {path}")
    return metadata


def guarded_text(path: Path | str, kb_root: Path | str) -> str:
    """Read a canonical queue after enforcing path and kb-bot ownership."""
    candidate = canonical_queue_path(path, kb_root)
    text = regular_text(candidate)
    metadata = _frontmatter(text, candidate)
    if metadata.get("author") != "kb-bot":
        raise WriteGuardError("queue target lacks author: kb-bot")
    if metadata.get("type") == "source" or candidate.name == "_dive-queue.md":
        raise WriteGuardError("source notes and generated dive queue are not mutable queues")
    return text


# Public for non-queue KB control files (notably ``_log.md``) that need the
# same duplicate-key-safe frontmatter parser without the queue-path allowlist.
frontmatter_metadata = _frontmatter
strict_frontmatter = _frontmatter


def _lock_candidate(root: Path, lock_path: Path | str | None) -> Path:
    expected = root / LOCK_NAME
    if lock_path is None:
        candidate = expected
    else:
        candidate = _absolute(lock_path, root)
    if candidate != expected:
        raise WriteGuardError("lock path must be KB-root/.kb-write.lock")
    return candidate


@contextlib.contextmanager
def write_lock(kb_root: Path | str, lock_path: Path | str | None = None,
               *, nonblocking: bool = False):
    """Hold the shared exclusive KB lock.

    The lock is created only by the checked ``os.open`` call.  No ``touch`` or
    other path-following pre-step is used.  ``nonblocking=True`` raises
    :class:`LockBusyError` when another writer currently owns it.
    """
    root = canonical_root(kb_root)
    candidate = _lock_candidate(root, lock_path)
    try:
        descriptor, _ = _open_regular(
            candidate,
            flags=os.O_RDWR | os.O_CREAT | _O_NONBLOCK,
            mode=0o600,
            allow_final_missing=True,
        )
    except OSError as exc:
        # Keep lock-acquisition failures distinct from OSErrors raised by a
        # writer's body; callers can report this as an unsafe lock without
        # swallowing a later write failure.
        raise WriteGuardError(f"cannot open KB lock: {exc}") from exc
    acquired = False
    try:
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise LockBusyError("another KB writer holds .kb-write.lock") from exc
        acquired = True
        yield descriptor
    finally:
        if acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        else:
            os.close(descriptor)


def atomic_create(path: Path | str, text: str, *, prefix: str = ".kb-create-") -> None:
    """Publish a complete new regular file without clobbering any existing path.

    Caller must hold the shared lock and validate its destination/frontmatter.
    A crash after publication can leave the temporary hardlink alongside the
    target; that is a recoverable partial state, not permission to overwrite.
    """
    if not isinstance(text, str):
        raise TypeError("atomic_create text must be str")
    candidate = _absolute(path)
    _walk_no_symlinks(candidate, allow_final_missing=True)
    descriptor, temporary = tempfile.mkstemp(prefix=prefix, dir=str(candidate.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _walk_no_symlinks(candidate, allow_final_missing=True)
        # Unlike replace/rename, link fails if ANY target already exists.
        os.link(temporary, candidate, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    directory_fd = os.open(candidate.parent, os.O_RDONLY | os.O_DIRECTORY | _O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_write(path: Path | str, text: str, *, prefix: str = ".kb-write-") -> None:
    """Atomically replace an existing regular file while preserving its mode."""
    if not isinstance(text, str):
        raise TypeError("atomic_write text must be str")
    candidate = _absolute(path)
    _walk_no_symlinks(candidate)
    descriptor, original = _open_regular(candidate, flags=os.O_RDONLY | _O_NONBLOCK)
    os.close(descriptor)
    temporary_fd = -1
    temporary_name: str | None = None
    try:
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=prefix,
            dir=str(candidate.parent),
            text=False,
        )
        os.fchmod(temporary_fd, stat.S_IMODE(original.st_mode))
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="") as handle:
            temporary_fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # Do not replace a path that changed identity after the first checked
        # open.  Cooperative writers already hold the lock; this also makes a
        # direct caller fail closed under a concurrent path replacement.
        current_fd, current = _open_regular(candidate, flags=os.O_RDONLY | _O_NONBLOCK)
        os.close(current_fd)
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise WriteGuardError(f"target changed while preparing atomic write: {candidate}")

        os.replace(temporary_name, candidate)
        temporary_name = None
        try:
            directory_fd = os.open(
                candidate.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _O_CLOEXEC | _O_NOFOLLOW,
            )
        except OSError:
            directory_fd = -1
        if directory_fd != -1:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_fd != -1:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


# Descriptive aliases make the contract convenient for callers that prefer
# explicit names while the short names above preserve existing tool style.
kb_write_lock = write_lock
guarded_queue_text = guarded_text
read_regular_text = regular_text
