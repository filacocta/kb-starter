#!/usr/bin/env python3
"""Race-safe promotion of a queue line between reading-list ladder sections.

The reading-list ladder (`## Pending → Approved → Auto-approved → Processed →
Failed — Retry`) distinguishes items by SECTION, not by checkbox mark — a
pending item and an approved item are both `- [ ]`. So "promoting" an item is a
section MOVE, which `kb-checkoff.py` (mark flip in place) and `kb-capture-url.py`
(append new) cannot do. This is the missing "move" verb.

It holds the shared `<kb-root>/.kb-write.lock` (same lock as kb-checkoff and
kb-capture), re-reads the file inside the lock, cuts the unique line matching
`--url` under the `--from` section, re-inserts it at the end of the `--to`
section, writes atomically (temp file + os.replace), then best-effort regenerates
`_dive-queue.md`. `###` sub-groups roll up into their parent `##` section, so an
item filed under a dated sub-heading inside `## Pending` still counts as Pending.

Usage:
    kb-promote.py --file topics/<t>/reading-list.md --url <url> \
        --from Pending --to Approved
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

LOCK_NAME = ".kb-write.lock"
CHECKBOX_RE = re.compile(r"^\s*-\s+\[[ xX!~?]\]\s")
# Canonical reading-list section ladder (see _schema.md / _rules.md).
CANONICAL = ["Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry"]


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


def queue_parser():
    key = "kb_promotion_queue_parser"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-queue-parse.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def has_url_whitespace_or_control(value: str) -> bool:
    return any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)


def guarded_text(path: Path, kb_root: Path) -> str:
    """Compatibility wrapper around the shared canonical queue guard."""
    return _write_guard.guarded_text(path, kb_root)


def atomic_write(path: Path, text: str) -> None:
    """Compatibility wrapper around the shared mode-preserving writer."""
    _write_guard.atomic_write(path, text, prefix=".kb-promote-")


def regenerate_dive_queue(kb_root: Path) -> None:
    """Best-effort refresh of the derived `_dive-queue.md` (runs after the lock
    is released; the derive script takes the same lock). Never raises."""
    script = Path(__file__).resolve().parent / "kb-derive-dive-queue.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--kb-root", str(kb_root), "--write"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as exc:  # pragma: no cover - best effort
        print(f"warning: dive-queue regen failed: {exc}", file=sys.stderr)


def find_in_section(lines: list[str], url: str, section: str) -> list[int]:
    """Return indices whose URL token exactly equals *url* under a section."""
    parsed = queue_parser().parse_queue("".join(lines))
    parsed.require_approval_safe()
    return [row.line - 1 for row in parsed.rows
            if row.mark == " " and row.section == section and row.locator == url]


def section_insert_index(lines: list[str], section: str) -> int | None:
    """End-of-section insertion point for `## section` (before the next `##`,
    trailing blanks trimmed), or None if the section header is absent."""
    return queue_parser().section_insert_index("".join(lines), section)


def promote(
    file_path: Path,
    url: str,
    from_section: str,
    to_section: str,
    lock_path: Path,
    kb_root: Path,
    dry_run: bool = False,
) -> tuple[bool, str, dict]:
    if has_url_whitespace_or_control(url):
        return False, "URL argument must not contain whitespace or control characters", {}
    if any("\n" in value or "\r" in value for value in (from_section, to_section)):
        return False, "queue arguments must be single-line", {}
    if from_section not in CANONICAL:
        return False, f"--from must be a canonical section {CANONICAL}, got {from_section!r}", {}
    if to_section not in CANONICAL:
        return False, f"--to must be a canonical section {CANONICAL}, got {to_section!r}", {}
    if from_section == to_section:
        return False, "--from and --to are the same section", {}

    try:
        root = _write_guard.canonical_root(kb_root)
    except (OSError, ValueError) as exc:
        return False, f"invalid KB root: {exc}", {}
    try:
        with _write_guard.write_lock(root, lock_path):
            try:
                text = guarded_text(file_path, root)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                return False, f"unsafe queue target: {exc}", {}
            lines = text.splitlines(keepends=True)

            hits = find_in_section(lines, url, from_section)
            if not hits:
                return False, f"no line with exact URL {url!r} under '## {from_section}'", {}
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} lines exactly match {url!r} under '## {from_section}'", {}
            idx = hits[0]

            if section_insert_index(lines, to_section) is None:
                return False, f"target section '## {to_section}' not found (run kb-lint; topics should have the ladder)", {}

            moved = lines[idx]
            if not moved.endswith("\n"):
                moved += "\n"
            moved_text = CHECKBOX_RE.sub("", moved, count=1).strip()
            info = {
                "file": str(file_path),
                "from": from_section,
                "to": to_section,
                "line": idx + 1,
                "moved_text": moved_text,
            }
            if dry_run:
                return True, f"[dry-run] would move line {idx + 1} '{from_section}' -> '{to_section}' in {file_path.name}", info

            del lines[idx]
            insert_at = section_insert_index(lines, to_section)  # recompute after delete
            assert insert_at is not None
            lines.insert(insert_at, moved)

            atomic_write(file_path, "".join(lines))
            info["line"] = insert_at + 1
            return True, f"moved line '{from_section}' -> '{to_section}' in {file_path.name}", info
    except _write_guard.WriteGuardError as exc:
        return False, f"unsafe KB lock: {exc}", {}
    except (ValueError, OSError, RuntimeError) as exc:
        return False, f"unsafe queue operation: {exc}", {}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Race-safe reading-list section promotion (move).")
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--file", required=True, help="reading-list file (relative to kb-root or absolute)")
    p.add_argument("--url", required=True, help="exact URL token identifying the line to move")
    p.add_argument("--from", dest="from_section", required=True, help="source section (canonical)")
    p.add_argument("--to", dest="to_section", required=True, help="target section (canonical)")
    p.add_argument("--dry-run", action="store_true", help="report the planned move without writing")
    p.add_argument("--json", action="store_true", help="emit a JSON result object on stdout")
    args = p.parse_args(argv)

    # Preserve the lexical root so the shared guard can reject symlinked roots
    # and parent traversal rather than silently following them.
    kb_root = Path(args.kb_root).expanduser().absolute()
    fp = Path(args.file)
    if not fp.is_absolute():
        fp = kb_root / fp
    if not fp.exists():
        print(f"error: file not found: {fp}", file=sys.stderr)
        return 2

    ok, msg, info = promote(
        fp, args.url, args.from_section, args.to_section,
        kb_root / LOCK_NAME, kb_root, dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps({"ok": ok, "message": msg, **info}))
    else:
        print(msg, file=sys.stdout if ok else sys.stderr)
    if ok and not args.dry_run:
        regenerate_dive_queue(kb_root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
