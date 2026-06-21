#!/usr/bin/env python3
"""Capture a URL into the KB inbox with agent/session provenance.

Routing (see _schema.md "Topic Reading Lists" + CLAUDE.md capture policy):
  --topic <slug> given AND topics/<slug>/reading-list.md exists -> append under that
      topic's `## Pending` section.
  otherwise -> append to readinglist/Links.md (the same global inbox the Telegram
      #unfiltered lane uses).

Provenance is tagged so agent-captured URLs are traceable and distinct from
Telegram-captured ones:  (via agent <name>, session <id>, YYYY-MM-DD)
vs Telegram's            (via Telegram #unfiltered, YYYY-MM-DD)

Dedups against already-ingested sources (kb-seen.py) and against an exact existing
line. Holds the shared .kb-write.lock so it never races kb-feed / kb-checkoff.

Usage:
  kb-capture-url.py <url> --source "agent claude" [--session <id>] [--topic <slug>] [--note "<why>"]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
LEVEL_2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
DEFAULT_KB_ROOT = Path("~/Vault/knowledgebase").expanduser()


@contextmanager
def kb_write_lock(kb_root: Path):
    lock_path = kb_root / ".kb-write.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("kb-capture-url: another KB writer holds .kb-write.lock; try again shortly.")
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def assert_kb_bot(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m or not re.search(r"^author:\s*kb-bot\s*$", m.group("body"), re.MULTILINE):
        sys.exit(f"kb-capture-url: {path} is not kb-bot authored; refusing to write (Rule 1).")


def already_ingested(kb_root: Path, url: str) -> str | None:
    seen = kb_root / "_tools" / "kb-seen.py"
    if not seen.exists():
        return None
    try:
        res = subprocess.run([sys.executable, str(seen), "--check", url],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # kb-seen emits JSON: {"ingested": {"<url>": <source-note-path or null>}}.
    # A null value means NOT ingested; a path string means already ingested.
    try:
        data = json.loads(res.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return None
    for value in (data.get("ingested") or {}).values():
        if isinstance(value, str) and value.strip():
            return value
    return None


def update_modified(text: str, today: date) -> str:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    start, end = m.span("body")
    body = m.group("body")
    if re.search(r"^modified:\s*.*$", body, re.MULTILINE):
        body = re.sub(r"^modified:\s*.*$", f"modified: {today.isoformat()}", body, count=1, flags=re.MULTILINE)
    else:
        body = f"{body}\nmodified: {today.isoformat()}"
    return f"{text[:start]}{body}{text[end:]}"


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_under_pending(path: Path, line: str, url: str, today: date) -> bool:
    """Insert under `## Pending`, before the next level-2 heading."""
    text = path.read_text(encoding="utf-8")
    if line in text.splitlines() or url in text:
        return False
    lines = text.splitlines()
    pending_idx = next((i for i, ln in enumerate(lines)
                        if (t := LEVEL_2_RE.match(ln)) and t.group("title").strip().lower() == "pending"), None)
    if pending_idx is None:
        sys.exit(f"kb-capture-url: {path} has no '## Pending' section (run kb-lint; topics should have the ladder).")
    insert_at = len(lines)
    for i in range(pending_idx + 1, len(lines)):
        if LEVEL_2_RE.match(lines[i]):
            insert_at = i
            break
    while insert_at > pending_idx + 1 and lines[insert_at - 1] == "":
        insert_at -= 1
    lines.insert(insert_at, line)
    atomic_write(path, update_modified("\n".join(lines) + "\n", today))
    return True


def append_to_end(path: Path, line: str, url: str, today: date) -> bool:
    text = path.read_text(encoding="utf-8")
    if line in text.splitlines() or url in text:
        return False
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    lines.append(line)
    atomic_write(path, update_modified("\n".join(lines) + "\n", today))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kb-capture-url")
    ap.add_argument("url")
    ap.add_argument("--source", default="agent", help='Provenance, e.g. "agent claude" or "deepseek".')
    ap.add_argument("--session", default=None, help="Session id for traceability.")
    ap.add_argument("--topic", default=None, help="Target topic slug (routes to its ## Pending).")
    ap.add_argument("--note", default=None, help="Optional why-saved note.")
    ap.add_argument("--kb-root", type=Path, default=DEFAULT_KB_ROOT)
    args = ap.parse_args(argv)

    if not re.match(r"^https?://", args.url):
        return 0  # silently ignore non-http(s) (hooks may pass odd values)
    kb_root = args.kb_root.expanduser()
    today = date.today()

    src = args.source.strip()
    if args.session:
        src = f"{src}, session {args.session}"
    annotation = f"(via {src}, {today.isoformat()})"
    note = (" ".join(args.note.split()) + " — ") if args.note else ""
    line = f"- [ ] {args.url} — {note}{annotation}"

    with kb_write_lock(kb_root):
        ingested = already_ingested(kb_root, args.url)
        if ingested:
            print(f"already ingested: {ingested}")
            return 0

        topic_rl = None
        if args.topic:
            candidate = kb_root / "topics" / args.topic / "reading-list.md"
            if candidate.exists():
                topic_rl = candidate
            else:
                print(f"topic '{args.topic}' has no reading-list; falling back to Links.md", file=sys.stderr)

        if topic_rl is not None:
            assert_kb_bot(topic_rl)
            wrote = append_under_pending(topic_rl, line, args.url, today)
            target = f"topics/{args.topic}/reading-list.md ## Pending"
        else:
            links = kb_root / "readinglist" / "Links.md"
            assert_kb_bot(links)
            wrote = append_to_end(links, line, args.url, today)
            target = "readinglist/Links.md"

    print(f"{'captured' if wrote else 'already queued'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
