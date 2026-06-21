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
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LOCK_NAME = ".kb-write.lock"
CHECKBOX_RE = re.compile(r"^\s*-\s+\[[ xX!~?]\]\s")
H2_RE = re.compile(r"^##[ \t]+(.+?)\s*$")
H23_RE = re.compile(r"^(#{2,3})[ \t]+(.+?)\s*$")
# Canonical reading-list section ladder (see _schema.md / _rules.md).
CANONICAL = ["Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry"]


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
    """Return indices of checkbox lines containing `url` under canonical `##
    section`. A `###` sub-group keeps its parent `##` section."""
    hits: list[int] = []
    canonical: str | None = None
    for i, line in enumerate(lines):
        heading = H23_RE.match(line.rstrip("\n"))
        if heading:
            if len(heading.group(1)) == 2:
                canonical = heading.group(2)
            continue
        if canonical == section and CHECKBOX_RE.match(line) and url in line:
            hits.append(i)
    return hits


def section_insert_index(lines: list[str], section: str) -> int | None:
    """End-of-section insertion point for `## section` (before the next `##`,
    trailing blanks trimmed), or None if the section header is absent."""
    head_idx = None
    for i, line in enumerate(lines):
        m = H2_RE.match(line.rstrip("\n"))
        if m and m.group(1) == section:
            head_idx = i
            break
    if head_idx is None:
        return None
    insert_at = len(lines)
    for i in range(head_idx + 1, len(lines)):
        if H2_RE.match(lines[i].rstrip("\n")):
            insert_at = i
            break
    while insert_at > head_idx + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    return insert_at


def promote(
    file_path: Path,
    url: str,
    from_section: str,
    to_section: str,
    lock_path: Path,
    kb_root: Path,
    dry_run: bool = False,
) -> tuple[bool, str, dict]:
    if from_section not in CANONICAL:
        return False, f"--from must be a canonical section {CANONICAL}, got {from_section!r}", {}
    if to_section not in CANONICAL:
        return False, f"--to must be a canonical section {CANONICAL}, got {to_section!r}", {}
    if from_section == to_section:
        return False, "--from and --to are the same section", {}

    lock_path.touch(exist_ok=True)
    with open(lock_path, "r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released on close
        try:
            text = file_path.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)

            hits = find_in_section(lines, url, from_section)
            if not hits:
                return False, f"no line containing {url!r} under '## {from_section}'", {}
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} lines contain {url!r} under '## {from_section}'", {}
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

            tmp = file_path.with_suffix(file_path.suffix + ".tmp")
            tmp.write_text("".join(lines), encoding="utf-8")
            os.replace(tmp, file_path)
            info["line"] = insert_at + 1
            return True, f"moved line '{from_section}' -> '{to_section}' in {file_path.name}", info
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Race-safe reading-list section promotion (move).")
    p.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    p.add_argument("--file", required=True, help="reading-list file (relative to kb-root or absolute)")
    p.add_argument("--url", required=True, help="substring identifying the line to move")
    p.add_argument("--from", dest="from_section", required=True, help="source section (canonical)")
    p.add_argument("--to", dest="to_section", required=True, help="target section (canonical)")
    p.add_argument("--dry-run", action="store_true", help="report the planned move without writing")
    p.add_argument("--json", action="store_true", help="emit a JSON result object on stdout")
    args = p.parse_args(argv)

    kb_root = Path(args.kb_root).expanduser().resolve()
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
