#!/usr/bin/env python3
"""Capture a URL into the KB inbox with agent/session provenance.

Routing (see _schema.md "Topic Reading Lists" + the capture policy):
  --topic <slug> given AND topics/<slug>/reading-list.md exists -> append under that
      topic's `## Pending` section.
  no --topic -> append to readinglist/Links.md. An explicitly requested topic must exist and pass
      the queue guard; it never silently falls back to the global inbox.

Provenance records the capturing agent and session:
  (via agent <name>, session <id>, YYYY-MM-DD)

Dedups against already-ingested sources (kb-seen.py) and against an exact existing
line. Queue writers share .kb-write.lock with kb-checkoff.

Usage:
  kb-capture-url.py <url> --source "agent" [--session <id>] [--topic <slug>] [--note "<why>"]
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
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
TOPIC_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HTTP_LOOKING_RE = re.compile(r"^https?(?::|/)", re.IGNORECASE)
HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
LOCK_NAME = ".kb-write.lock"
DEFAULT_KB_ROOT = Path(__file__).resolve().parents[1]


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
    key = "kb_capture_queue_parser"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-queue-parse.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


class DedupError(RuntimeError):
    """The required ingestion-level dedup check did not complete safely."""


def url_whitespace_or_control(value: str) -> bool:
    return any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)


def http_url_error(url: str) -> str | None:
    """Return an error for HTTP-looking malformed URLs, or None if valid/unhandled."""
    if not HTTP_LOOKING_RE.match(url):
        return None
    if url_whitespace_or_control(url):
        return "URL must not contain whitespace or control characters"
    if not HTTP_SCHEME_RE.match(url):
        return "URL must use an http:// or https:// scheme"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return "URL has an invalid HTTP host"
    if not parsed.netloc or not hostname:
        return "URL must include a host"
    return None


@contextmanager
def kb_write_lock(kb_root: Path):
    manager = _write_guard.write_lock(kb_root, nonblocking=True)
    try:
        manager.__enter__()
    except _write_guard.LockBusyError as exc:
        raise SystemExit(
            "kb-capture-url: another KB writer holds .kb-write.lock; try again shortly."
        ) from exc
    except (OSError, _write_guard.WriteGuardError) as exc:
        raise SystemExit(f"kb-capture-url: invalid KB lock path: {exc}") from exc
    try:
        yield
    except BaseException:
        manager.__exit__(*sys.exc_info())
        raise
    else:
        manager.__exit__(None, None, None)


def guarded_text(path: Path, kb_root: Path) -> str:
    """Compatibility wrapper around the shared canonical queue guard."""
    return _write_guard.guarded_text(path, kb_root)


def has_exact_queue_url(text: str, url: str) -> bool:
    """Return whether a queue row contains exactly *url* as its URL token."""
    return any(row.locator == url for row in queue_parser().parse_queue(text).rows)


def already_ingested(kb_root: Path, url: str) -> str | None:
    seen = kb_root / "_tools" / "kb-seen.py"
    if not seen.is_file() or seen.is_symlink():
        raise DedupError(f"dedup helper unavailable: {seen}")
    try:
        res = subprocess.run(
            [sys.executable, str(seen), "--kb-root", str(kb_root), "--check", url],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DedupError(f"dedup check failed: {exc}") from exc
    if res.returncode != 0:
        raise DedupError(f"dedup check exited with status {res.returncode}")
    # kb-seen emits JSON: {"ingested": {"<url>": <source-note-path or null>}}.
    # A null value means NOT ingested; a path string means already ingested.
    try:
        data = json.loads(res.stdout or "")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DedupError("dedup check returned invalid JSON") from exc
    if not isinstance(data, dict) or set(data) != {"ingested"} or not isinstance(data.get("ingested"), dict):
        raise DedupError("dedup check returned an invalid result shape")
    ingested = data["ingested"]
    if set(ingested) != {url}:
        raise DedupError("dedup check did not report the requested URL")
    value = ingested[url]
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    raise DedupError("dedup check returned an invalid source-note path")


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
    """Compatibility wrapper around the shared mode-preserving writer."""
    _write_guard.atomic_write(path, text, prefix=".kb-capture-")


def validate_inserted_row(text: str, line: str, section: str | None = None) -> None:
    parsed = queue_parser().parse_queue(text)
    if parsed.errors or sum(row.raw == line and (section is None or row.section == section)
                            for row in parsed.rows) != 1:
        raise ValueError("capture text does not produce one real queue row in its intended section")


def append_under_pending(path: Path, line: str, url: str, today: date) -> bool:
    """Insert under `## Pending`, before the next level-2 heading."""
    if any("\n" in value or "\r" in value for value in (line, url)):
        raise ValueError("queue arguments must be single-line")
    text = _write_guard.regular_text(path)
    insert_at = queue_parser().section_insert_index(text, "Pending")
    if insert_at is None:
        raise ValueError(f"{path} has no canonical Pending section")
    if has_exact_queue_url(text, url):
        return False
    lines = text.splitlines(keepends=True)
    if insert_at and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, line + "\n")
    updated = update_modified("".join(lines), today)
    validate_inserted_row(updated, line, "Pending")
    atomic_write(path, updated)
    return True


def append_to_end(path: Path, line: str, url: str, today: date) -> bool:
    if any("\n" in value or "\r" in value for value in (line, url)):
        raise ValueError("queue arguments must be single-line")
    text = _write_guard.regular_text(path)
    parsed = queue_parser().parse_queue(text)
    if parsed.errors:
        raise ValueError("unsafe inbox structure: " + "; ".join(parsed.errors))
    if has_exact_queue_url(text, url):
        return False
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    lines.append(line)
    updated = update_modified("\n".join(lines) + "\n", today)
    validate_inserted_row(updated, line)
    atomic_write(path, updated)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kb-capture-url")
    ap.add_argument("url")
    ap.add_argument("--source", default="agent", help="Provenance label for the capture.")
    ap.add_argument("--session", default=None, help="Session id for traceability.")
    ap.add_argument("--topic", default=None, help="Target topic slug (routes to its ## Pending).")
    ap.add_argument("--note", default=None, help="Optional why-saved note.")
    ap.add_argument("--kb-root", type=Path, default=DEFAULT_KB_ROOT)
    args = ap.parse_args(argv)

    values = (args.url, args.source, args.session or "", args.topic or "", args.note or "")
    if any("\n" in value or "\r" in value for value in values):
        print("kb-capture-url: queue arguments must be single-line", file=sys.stderr)
        return 2
    if not HTTP_LOOKING_RE.match(args.url):
        return 0  # silently ignore non-http(s) (hooks may pass odd values)
    if (url_error := http_url_error(args.url)) is not None:
        print(f"kb-capture-url: {url_error}", file=sys.stderr)
        return 2
    if args.topic and not TOPIC_SLUG_RE.fullmatch(args.topic):
        print(f"kb-capture-url: invalid topic slug: {args.topic!r}", file=sys.stderr)
        return 2
    # Keep the caller's lexical path so canonical_root can reject a root
    # reached through a symlink instead of silently accepting its destination.
    kb_root = args.kb_root.expanduser().absolute()
    today = date.today()

    src = args.source.strip()
    if args.session:
        src = f"{src}, session {args.session}"
    annotation = f"(via {src}, {today.isoformat()})"
    note = (" ".join(args.note.split()) + " — ") if args.note else ""
    line = f"- [ ] {args.url} — {note}{annotation}"

    with kb_write_lock(kb_root):
        if args.topic:
            target = kb_root / "topics" / args.topic / "reading-list.md"
            target_label = f"topics/{args.topic}/reading-list.md ## Pending"
        else:
            target = kb_root / "readinglist" / "Links.md"
            target_label = "readinglist/Links.md"

        try:
            guarded_text(target, kb_root)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            if args.topic:
                print(f"kb-capture-url: invalid requested topic '{args.topic}': {exc}", file=sys.stderr)
            else:
                print(f"kb-capture-url: unsafe queue target: {exc}", file=sys.stderr)
            return 1

        try:
            ingested = already_ingested(kb_root, args.url)
        except DedupError as exc:
            print(f"kb-capture-url: refusing to write without a valid dedup result: {exc}", file=sys.stderr)
            return 1
        if ingested:
            print(f"already ingested: {ingested}")
            return 0

        try:
            if args.topic:
                wrote = append_under_pending(target, line, args.url, today)
            else:
                wrote = append_to_end(target, line, args.url, today)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"kb-capture-url: unsafe queue operation: {exc}", file=sys.stderr)
            return 1

    print(f"{'captured' if wrote else 'already queued'} -> {target_label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
