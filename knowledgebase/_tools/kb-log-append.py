#!/usr/bin/env python3
"""Lock-safe, idempotent append for KB activity-log entries.

The marker makes retries safe: an operation may append at most one entry even
when several agents resume or report completion at nearly the same time.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import re
import sys
from pathlib import Path


LOCK_NAME = ".kb-write.lock"
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def _load_write_guard():
    """Load the hyphenated shared guard without requiring a package install."""
    key = "_kb_write_guard"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-write-guard.py"))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-write-guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


_write_guard = _load_write_guard()


def marker(operation_id: str) -> str:
    return f"<!-- kb-operation-id: {operation_id} -->"


def ensure_kb_authored_log(log_path: Path) -> None:
    text = _write_guard.regular_text(log_path)
    metadata = _write_guard.frontmatter_metadata(text, log_path)
    if metadata.get("author") != "kb-bot":
        raise ValueError("_log.md is not a kb-bot-authored KB log")


def atomic_write(path: Path, text: str) -> None:
    _write_guard.atomic_write(path, text, prefix=".kb-log-")


def append_entry(kb_root: Path, operation_id: str, entry: str, dry_run: bool = False) -> tuple[bool, str]:
    """Append exactly once. Return ``(appended, message)``.

    An existing marker is a successful idempotent result, not an error.
    """
    if not OPERATION_ID_RE.fullmatch(operation_id):
        raise ValueError("operation id must be 3–128 characters of letters, digits, dot, underscore, colon, or hyphen")
    if not entry.strip():
        raise ValueError("log entry must not be empty")
    if "kb-operation-id:" in entry:
        raise ValueError("log entry must not contain a kb-operation-id marker")

    kb_root = _write_guard.canonical_root(kb_root)
    log_path = kb_root / "_log.md"
    if not log_path.exists():
        raise FileNotFoundError(f"log not found: {log_path}")
    lock_path = kb_root / LOCK_NAME
    with _write_guard.write_lock(kb_root, lock_path):
        ensure_kb_authored_log(log_path)
        current = _write_guard.regular_text(log_path)
        entry_marker = marker(operation_id)
        if entry_marker in current:
            return False, f"already appended: {operation_id}"
        if dry_run:
            return False, f"would append: {operation_id}"
        updated = current.rstrip() + "\n\n" + entry_marker + "\n\n" + entry.strip() + "\n"
        atomic_write(log_path, updated)
        return True, f"appended: {operation_id}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lock-safe, idempotent append to KB _log.md.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--operation-id", required=True, help="Stable retry-safe identifier for this operation.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--entry", help="Markdown entry text, excluding the operation marker.")
    source.add_argument("--entry-file", help="Read Markdown entry text from this file.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        entry = args.entry if args.entry is not None else Path(args.entry_file).read_text(encoding="utf-8")
        # Preserve the lexical root so canonical_root can reject symlinked
        # roots instead of silently accepting their destination.
        appended, message = append_entry(Path(args.kb_root).expanduser().absolute(), args.operation_id, entry, args.dry_run)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
