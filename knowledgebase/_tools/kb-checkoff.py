#!/usr/bin/env python3
"""Race-safe check-off of a queue line in a KB markdown file.

Holds an exclusive lock on `<kb-root>/.kb-write.lock`, re-reads the target file
inside the lock, flips the matching checkbox item to `- [x]` (or `- [!]`),
appends a note, and writes atomically (temp file + os.replace). This is the
single safe path for skills AND kb-feed to mutate queue files concurrently.

Usage:
    kb-checkoff.py --file topics/<t>/reading-list.md --url <url> --mark x \
        --note "ingested 2026-06-03 -> [[...]]"
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import os
import re
import subprocess
import sys
from pathlib import Path

LOCK_NAME = ".kb-write.lock"
CHECKBOX_RE = re.compile(r"^(\s*-\s+\[)([ xX!~?])(\]\s*)")
MARKS = {" ", "x", "!", "~", "?"}


def normalize_mark(mark: str) -> str:
    value = mark.lower()
    if value not in MARKS:
        raise ValueError(f"invalid checkbox mark: {mark!r}")
    return value


def regenerate_dive_queue(kb_root: Path) -> None:
    """Best-effort refresh of the derived `_dive-queue.md` dashboard.

    Runs as a subprocess after our lock is released (the derive script takes
    the same `.kb-write.lock`; calling it inline while holding the lock would
    deadlock). Never raises — a stale dashboard is preferable to a failed
    check-off.
    """
    script = Path(__file__).resolve().parent / "kb-derive-dive-queue.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--kb-root", str(kb_root), "--write"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as exc:
        print(f"warning: dive-queue regen failed: {exc}", file=sys.stderr)


def append_ideas_backlog(backlog_path: Path, routed_body: str, source_file: Path, source_line: int) -> None:
    today = dt.date.today().isoformat()
    rel = source_file
    entry = (
        f"- [ ] {routed_body} — routed from `{rel}` line {source_line} on {today}\n"
    )
    with open(backlog_path, "a", encoding="utf-8") as fh:
        fh.write(entry)


def checkoff(
    file_path: Path,
    url: str,
    mark: str,
    note: str,
    lock_path: Path,
    ideas_backlog: Path | None = None,
    kb_root: Path | None = None,
    from_mark: str = " ",
) -> tuple[bool, str]:
    mark = normalize_mark(mark)
    from_mark = normalize_mark(from_mark)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released on close
        try:
            text = file_path.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)
            hits = []
            for i, line in enumerate(lines):
                match = CHECKBOX_RE.match(line)
                if match and match.group(2).lower() == from_mark and url in line:
                    hits.append(i)
            if not hits:
                return False, f"no [{from_mark}] line containing: {url}"
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} [{from_mark}] lines contain {url}"
            i = hits[0]
            line = lines[i]
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            routed_body = CHECKBOX_RE.sub("", body, count=1).strip()
            body = CHECKBOX_RE.sub(rf"\g<1>{mark}\g<3>", body, count=1).rstrip()
            if note:
                body = f"{body} — {note}"
            lines[i] = body + newline
            tmp = file_path.with_suffix(file_path.suffix + ".tmp")
            tmp.write_text("".join(lines), encoding="utf-8")
            os.replace(tmp, file_path)
            if ideas_backlog is not None:
                source_ref = file_path
                if kb_root is not None:
                    try:
                        source_ref = file_path.relative_to(kb_root)
                    except ValueError:
                        pass
                append_ideas_backlog(ideas_backlog, routed_body, source_ref, i + 1)
            return True, f"marked [{mark}] line {i + 1} in {file_path.name}"
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Race-safe queue-line check-off.")
    p.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    p.add_argument("--file", required=True, help="queue file (relative to kb-root or absolute)")
    p.add_argument("--url", required=True, help="substring identifying the unchecked line")
    p.add_argument("--from-mark", choices=[" ", "x", "X", "!", "~", "?"], default=" ")
    p.add_argument("--mark", choices=["x", "!", "~", "?"], default="x")
    p.add_argument("--note", default="")
    p.add_argument(
        "--route-ideas-backlog",
        action="store_true",
        help="also append the original checked-off item to ideas/_backlog.md under the same lock",
    )
    args = p.parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    fp = Path(args.file)
    if not fp.is_absolute():
        fp = kb_root / fp
    if not fp.exists():
        print(f"error: file not found: {fp}", file=sys.stderr)
        return 2
    ideas_backlog = kb_root / "ideas" / "_backlog.md" if args.route_ideas_backlog else None
    if ideas_backlog is not None and not ideas_backlog.exists():
        print(f"error: ideas backlog not found: {ideas_backlog}", file=sys.stderr)
        return 2
    ok, msg = checkoff(
        fp,
        args.url,
        args.mark,
        args.note,
        kb_root / LOCK_NAME,
        ideas_backlog=ideas_backlog,
        kb_root=kb_root,
        from_mark=args.from_mark,
    )
    print(msg, file=sys.stderr if not ok else sys.stdout)
    if ok:
        regenerate_dive_queue(kb_root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
