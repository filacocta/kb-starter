#!/usr/bin/env python3
"""Derive the root `_dive-queue.md` dashboard from canonical topic queues.

Inputs:
- unchecked items under each topic reading-list's `## Auto-approved` section
- unchecked items in each topic's `research-ideas.md`

Default behavior is dry-run: print the would-be `_dive-queue.md` to stdout and
do not touch the live file. Use `--write` only after reviewing the output.
Writes use the shared KB lock and filesystem guard; in-process callers that
already hold that lock can call ``regenerate(..., lock_fd=descriptor)``.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import stat
import sys
from datetime import date
from pathlib import Path


LOCK_NAME = ".kb-write.lock"
_PARSE_WARNINGS_EMITTED: set[tuple[str, tuple[str, ...]]] = set()


def _load_queue_parser():
    """Load the shared pure queue parser despite its hyphenated filename."""
    key = "kb_derive_queue_parser"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).with_name("kb-queue-parse.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-queue-parse.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _load_write_guard():
    """Load the shared filesystem guard despite this tool's hyphenated name."""
    key = "_kb_write_guard"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).with_name("kb-write-guard.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-write-guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


_write_guard = _load_write_guard()


def _canonical_root(kb_root: Path | str) -> Path:
    """Return the checked, lexical KB root used by every writer operation."""
    return _write_guard.canonical_root(kb_root)


def _target_path(kb_root: Path | str) -> tuple[Path, Path, bool]:
    """Validate the root and the existing generated target's ownership.

    ``guarded_text`` deliberately rejects ``_dive-queue.md`` because it is a
    generated artifact, so this writer uses the guard's checked regular-file
    reader plus its strict duplicate-key-safe YAML parser directly.
    """
    root = _canonical_root(kb_root)
    target = root / "_dive-queue.md"
    try:
        text = _write_guard.regular_text(target)
    except FileNotFoundError:
        # A first generation is allowed, but creation remains a no-overwrite
        # operation performed by the guard after output validation.  Any
        # existing symlink/FIFO/hardlink is rejected by regular_text rather
        # than being mistaken for a missing target.
        return root, target, False
    metadata = _write_guard.frontmatter_metadata(text, target)
    if metadata.get("author") != "kb-bot":
        raise _write_guard.WriteGuardError(
            "generated dive queue target lacks author: kb-bot"
        )
    if metadata.get("type") != "meta":
        raise _write_guard.WriteGuardError(
            "generated dive queue target must have type: meta"
        )
    return root, target, True


def _validate_output(output: str, target: Path) -> None:
    """Require generated output to carry the same strict meta envelope."""
    if not isinstance(output, str):
        raise TypeError("dive queue output must be str")
    metadata = _write_guard.frontmatter_metadata(output, target)
    if metadata.get("author") != "kb-bot":
        raise _write_guard.WriteGuardError(
            "generated dive queue output lacks author: kb-bot"
        )
    if metadata.get("type") != "meta":
        raise _write_guard.WriteGuardError(
            "generated dive queue output must have type: meta"
        )


def _validate_lock_descriptor(root: Path, lock_fd: int) -> None:
    """Validate an explicit descriptor handed off by a lock-owning caller.

    The descriptor must be the regular, singly-linked canonical
    ``<root>/.kb-write.lock`` file.  The caller remains responsible for
    holding the shared ``flock``; passing the descriptor makes that contract
    explicit and avoids an environment variable that any child process could
    forge.  Descriptor identity is rechecked immediately before rendering and
    writing.
    """
    if not isinstance(lock_fd, int) or isinstance(lock_fd, bool) or lock_fd < 0:
        raise _write_guard.WriteGuardError("lock_fd must be an open file descriptor")
    lock_path = root / LOCK_NAME
    # Root canonicalisation has already checked the parent.  lstat-equivalent
    # ``follow_symlinks=False`` on the final component rejects a substituted
    # symlink without opening or touching it.
    try:
        path_info = os.stat(lock_path, follow_symlinks=False)
        fd_info = os.fstat(lock_fd)
    except OSError as exc:
        raise _write_guard.WriteGuardError(
            f"cannot validate shared lock descriptor: {exc}"
        ) from exc
    if not stat.S_ISREG(path_info.st_mode) or not stat.S_ISREG(fd_info.st_mode):
        raise _write_guard.WriteGuardError("shared lock must be a regular file")
    if path_info.st_nlink != 1 or fd_info.st_nlink != 1:
        raise _write_guard.WriteGuardError(
            "multiply-linked shared lock is not allowed"
        )
    if (path_info.st_dev, path_info.st_ino) != (fd_info.st_dev, fd_info.st_ino):
        raise _write_guard.WriteGuardError(
            "lock descriptor is not KB-root/.kb-write.lock"
        )
    try:
        # A descriptor handed over by ``write_lock`` can be checked without
        # opening a second path.  On an already-held descriptor this is a
        # no-op; an unheld descriptor acquires the lock here, which still
        # leaves the write protected and leaves release ownership with the
        # caller.  A different process's lock fails closed.
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise _write_guard.WriteGuardError(
            f"lock descriptor does not hold the shared KB lock: {exc}"
        ) from exc


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[: end + 5], text[end + 5 :]


def topic_slug(topic_dir: Path) -> str:
    return topic_dir.name


def title_for_topic(slug: str) -> str:
    return slug.replace("-", " ").title()


def _safe_input_text(path: Path, *, kb_root: Path | None = None) -> str:
    """Read one queue source through the shared regular-file/YAML guard."""
    try:
        if kb_root is not None and path.name == "reading-list.md":
            text = _write_guard.guarded_text(path, kb_root)
        else:
            text = _write_guard.regular_text(path)
            # The pure queue parser accepts both newline conventions; normalize
            # only the metadata validation input so a CRLF source is not
            # rejected before semantic parsing.  The returned text remains
            # byte-faithful for row extraction.
            metadata = _write_guard.frontmatter_metadata(text.replace("\r\n", "\n"), path)
            if metadata.get("author") != "kb-bot":
                raise _write_guard.WriteGuardError(
                    f"queue source lacks author: kb-bot: {path}"
                )
    except FileNotFoundError:
        return ""
    return text


def _safe_input_lines(path: Path, *, kb_root: Path | None = None) -> list[str]:
    """Compatibility helper returning source lines without line endings."""
    return _safe_input_text(path, kb_root=kb_root).splitlines()


def _row_parent_heading(parsed, row):
    """Return the nearest semantic level-two heading before one parsed row."""
    for heading in reversed(parsed.headings):
        if heading.line < row.line:
            return heading
    return None


def _warn_skipped_input(path: Path, errors: tuple[str, ...]) -> None:
    """Make a skipped queue visible without repeating one warning per call."""
    key = (str(path), errors)
    if key in _PARSE_WARNINGS_EMITTED:
        return
    _PARSE_WARNINGS_EMITTED.add(key)
    print(
        f"warning: derive skipped {path} because queue parsing is ambiguous: "
        + "; ".join(errors),
        file=sys.stderr,
    )


def unchecked_section_items(
    path: Path, section_name: str, *, kb_root: Path | None = None
) -> list[str]:
    text = _safe_input_text(path, kb_root=kb_root)
    parsed = _load_queue_parser().parse_queue(text)
    # A generated dashboard must never promote a partially interpreted or
    # structurally ambiguous queue.  Lint reports these diagnostics; derive
    # simply omits the affected input until it is repaired.
    if parsed.errors:
        _warn_skipped_input(path, parsed.errors)
        return []

    folded = section_name.casefold()
    canonical_by_folded = {
        name.casefold(): name for name in _load_queue_parser().CANONICAL_SECTIONS
    }
    canonical = canonical_by_folded.get(folded)
    if canonical is not None:
        # Only exact canonical headings are active approval sections.  A
        # lowercase/duplicate/fenced ``## Auto-approved`` cannot manufacture
        # an approved dashboard item (duplicates are already in ``errors``).
        return [row.raw.strip() for row in parsed.rows if row.mark == " " and row.section == canonical]

    if path.name == "research-ideas.md":
        # Research ideas are deliberately prose-only and may be grouped under
        # any level-two heading (or appear before the first one).  They are not
        # queue approvals, so include every real unchecked checkbox row after
        # the parser has excluded frontmatter/examples and surfaced errors.
        return [row.raw.strip() for row in parsed.rows if row.mark == " "]

    # Compatibility for callers asking about a named non-queue section in
    # another file: the shared parser still supplies semantic H2 boundaries
    # and real checkbox rows, so examples in fences/comments, blockquotes, and
    # indented code remain excluded without a second regex parser.
    items: list[str] = []
    for row in parsed.rows:
        if row.mark != " ":
            continue
        heading = _row_parent_heading(parsed, row)
        if heading is not None and heading.name.casefold() == folded:
            items.append(row.raw.strip())
    return items


def collect_topic(topic_dir: Path, *, kb_root: Path | None = None) -> tuple[list[str], list[str]]:
    auto_items = unchecked_section_items(
        topic_dir / "reading-list.md", "Auto-approved", kb_root=kb_root
    )
    idea_items = unchecked_section_items(
        topic_dir / "research-ideas.md", "Ideas", kb_root=kb_root
    )
    return auto_items, idea_items


def render(kb_root: Path, today: date | None = None) -> str:
    """Render the dashboard from a checked KB root without writing it."""
    kb_root = _canonical_root(kb_root)
    stamp = (today or date.today()).isoformat()
    parts: list[str] = [
        "---",
        "author: kb-bot",
        f"created: {stamp}",
        f"modified: {stamp}",
        "type: meta",
        "---",
        "",
        "# Research Dive Queue",
        "",
        "> GENERATED FILE - DO NOT EDIT. Derived by `_tools/kb-derive-dive-queue.py` from topic `## Auto-approved` reading-list sections and `research-ideas.md` files.",
        "",
        "This dashboard is a read-only view. Add exact source URLs to topic `reading-list.md` files, and add prose-only rabbit holes to `topics/<topic>/research-ideas.md`.",
        "",
        "---",
        "",
    ]

    any_items = False
    topics_root = kb_root / "topics"
    try:
        topics_info = os.stat(topics_root, follow_symlinks=False)
    except FileNotFoundError:
        topic_dirs: list[Path] = []
    else:
        if stat.S_ISLNK(topics_info.st_mode):
            raise _write_guard.WriteGuardError(
                f"topics directory must not be a symlink: {topics_root}"
            )
        if not stat.S_ISDIR(topics_info.st_mode):
            raise _write_guard.WriteGuardError(
                f"topics path must be a directory: {topics_root}"
            )
        topic_dirs = []
        for topic_dir in topics_root.iterdir():
            info = os.lstat(topic_dir)
            if stat.S_ISLNK(info.st_mode):
                raise _write_guard.WriteGuardError(
                    f"topic directory must not be a symlink: {topic_dir}"
                )
            if stat.S_ISDIR(info.st_mode):
                topic_dirs.append(topic_dir)

    for topic_dir in sorted(topic_dirs):
        auto_items, idea_items = collect_topic(topic_dir, kb_root=kb_root)
        if not auto_items and not idea_items:
            continue
        any_items = True
        slug = topic_slug(topic_dir)
        parts.extend([f"## {slug}", ""])
        if auto_items:
            parts.extend(["### Auto-approved sources", ""])
            parts.extend(auto_items)
            parts.append("")
        if idea_items:
            parts.extend(["### Research ideas", ""])
            parts.extend(idea_items)
            parts.append("")

    if not any_items:
        parts.extend(["*(empty)*", ""])

    return "\n".join(parts).rstrip() + "\n"


def _write_output(target: Path, output: str, *, target_exists: bool) -> None:
    """Validate and atomically write the checked generated target."""
    _validate_output(output, target)
    if target_exists:
        _write_guard.atomic_write(target, output, prefix=".kb-dive-queue-")
    else:
        _write_guard.atomic_create(target, output, prefix=".kb-dive-queue-")


def write_output(target: Path, output: str) -> None:
    """Compatibility wrapper for the old direct writer helper.

    The target is still constrained to a canonical root-level generated file;
    callers that need the normal lock/render transaction should use
    :func:`write_generated` or :func:`regenerate` instead.
    """
    if target.name != "_dive-queue.md":
        raise _write_guard.WriteGuardError(
            "generated target must be named _dive-queue.md"
        )
    if not isinstance(output, str):
        raise TypeError("dive queue output must be str")
    root = _canonical_root(target.parent)
    if root / "_dive-queue.md" != target:
        raise _write_guard.WriteGuardError("generated target is not canonical")
    write_generated(root, output)


def regenerate(kb_root: Path | str, lock_fd: int) -> None:
    """Regenerate while a caller holds the shared KB lock.

    ``lock_fd`` must be the descriptor yielded by
    ``kb-write-guard.write_lock(kb_root)``.  The descriptor is checked against
    the canonical lock inode before the target is read, rendered, and replaced;
    no environment variable is accepted as a lock claim.  The caller keeps
    ownership of the lock and is responsible for releasing it.
    """
    root = _canonical_root(kb_root)
    _validate_lock_descriptor(root, lock_fd)
    _, target, target_exists = _target_path(root)
    # Rendering is deliberately inside the caller's critical section.  Queue
    # writers may have changed an input immediately before this call.
    output = render(root)
    _validate_lock_descriptor(root, lock_fd)
    _write_output(target, output, target_exists=target_exists)


def regenerate_under_lock(kb_root: Path | str, lock_fd: int) -> None:
    """Backward/explicit-name alias for :func:`regenerate`."""
    regenerate(kb_root, lock_fd=lock_fd)


def write_generated(kb_root: Path | str, output: str | None = None) -> None:
    """Render and atomically write the generated queue under the shared lock.

    When ``output`` is omitted (the CLI path), rendering occurs after lock
    acquisition so the input snapshot cannot race a queue mutation.  The
    optional output argument preserves the historical helper signature for
    callers that already prepared a specific rendered payload; the write is
    still guarded and atomic.
    """
    root = _canonical_root(kb_root)
    with _write_guard.write_lock(root) as lock_fd:
        _validate_lock_descriptor(root, lock_fd)
        _, target, target_exists = _target_path(root)
        if output is None:
            output = render(root)
        _validate_lock_descriptor(root, lock_fd)
        _write_output(target, output, target_exists=target_exists)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the derived `_dive-queue.md` dashboard.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--dry-run", action="store_true", help="print output without writing; default behavior")
    parser.add_argument("--write", action="store_true", help="write `_dive-queue.md` after review")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser()
    if args.write:
        write_generated(kb_root)
        print(f"wrote {_canonical_root(kb_root) / '_dive-queue.md'}")
        return 0
    output = render(kb_root)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
