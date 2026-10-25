#!/usr/bin/env python3
"""Race-safe check-off of a queue line in a KB markdown file.

Holds an exclusive lock on `<kb-root>/.kb-write.lock`, re-reads the target file
inside the lock, flips the matching checkbox item to `- [x]` (or `- [!]`),
appends a note, and writes atomically (temp file + os.replace). This is the
shared check-off path for cooperating queue writers.

Usage:
    kb-checkoff.py --file topics/<t>/reading-list.md --url <url> --mark x \
        --note "ingested 2026-06-03 -> [[...]]"
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml

LOCK_NAME = ".kb-write.lock"
CHECKBOX_RE = re.compile(r"^(\s*-\s+\[)([ xX!~?])(\]\s*)")
MARKS = {" ", "x", "!", "~", "?"}
LOCATOR_PREFIXES = ("http://", "https://")
PROSE_QUEUE_PATHS = {
    Path("readinglist/Links.md"),
    Path("readinglist/topics.md"),
    Path("ideas/_backlog.md"),
}


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


def _source_completion_module():
    """Load the shared primary-locator parser and completion gate."""
    key = "kb_source_completion"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-source-completion.py"))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-source-completion.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _queue_parser_module():
    """Load the shared fence-aware queue parser."""
    key = "kb_queue_parser"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-queue-parse.py"))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-queue-parse.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def guarded_text(path: Path, kb_root: Path) -> str:
    """Compatibility wrapper around the shared canonical queue guard."""
    return _write_guard.guarded_text(path, kb_root)


def atomic_write(path: Path, text: str) -> None:
    """Compatibility wrapper around the shared mode-preserving writer."""
    _write_guard.atomic_write(path, text, prefix=".kb-checkoff-")


def normalize_mark(mark: str) -> str:
    value = mark.lower()
    if value not in MARKS:
        raise ValueError(f"invalid checkbox mark: {mark!r}")
    return value


def _primary_locator(row: str) -> str | None:
    """Return only a queue row's exact primary URL or absolute-PDF token."""
    return _source_completion_module().primary_locator(row)


def _selector_kind(selector: str) -> str:
    """Validate a selector and classify it as a locator or prose selector.

    Locator selectors deliberately use lexical equality with the queue row's
    primary token.  They must not pass through URL normalization or alias
    lookup: those operations belong to source identity validation after the
    row has been selected.
    """
    if not isinstance(selector, str):
        raise ValueError("selector must be text")
    if not selector or selector != selector.strip():
        raise ValueError("selector must be non-empty and have no surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in selector):
        raise ValueError("selector must not contain control characters")

    lowered = selector.lower()
    if lowered.startswith(LOCATOR_PREFIXES):
        if _primary_locator(f"- [ ] {selector}") != selector:
            raise ValueError("malformed HTTP(S) selector")
        try:
            parsed = urlsplit(selector)
            hostname = parsed.hostname
            # Accessing port validates malformed bracketed/port syntax without
            # changing the selector's spelling.
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("malformed HTTP(S) selector") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
            raise ValueError("malformed HTTP(S) selector")
        return "locator"

    if selector.startswith("/"):
        if _primary_locator(f"- [ ] {selector}") != selector:
            raise ValueError("malformed absolute-PDF selector")
        return "locator"

    return "prose"


def _prose_selector_allowed(file_path: Path, kb_root: Path) -> bool:
    """Allow prose selectors only in the global inboxes and ideas backlog."""
    try:
        queue_path = _write_guard.canonical_queue_path(file_path, kb_root)
        relative = queue_path.relative_to(kb_root)
    except (OSError, ValueError):
        return False
    return relative in PROSE_QUEUE_PATHS


def _row_looks_like_locator(line: str) -> bool:
    """Detect malformed locator-leading rows so prose cannot select them."""
    body = re.sub(r"^\s*-\s+\[[ xX!~?]\]\s*", "", line, count=1)
    first = body.split(None, 1)[0] if body else ""
    return first.lower().startswith(LOCATOR_PREFIXES) or first.startswith("/")


def _matches_selector(
    line: str,
    selector: str,
    selector_kind: str,
    *,
    prose_allowed: bool,
    parsed_row=None,
) -> bool:
    """Match a selector without crossing the URL/prose row boundary."""
    if not CHECKBOX_RE.match(line):
        return False
    locator = parsed_row.locator if parsed_row is not None else _primary_locator(line)
    if selector_kind == "locator":
        return locator == selector
    if not prose_allowed or locator is not None or _row_looks_like_locator(line):
        return False
    body = CHECKBOX_RE.sub("", line, count=1)
    return selector in body


def _proposed_queue_ok(
    text: str,
    line_number: int,
    selector: str,
    selector_kind: str,
    *,
    prose_allowed: bool,
    expected_mark: str,
    expected_row: str | None = None,
) -> bool:
    """Check that a pending write did not hide or change its selected row."""
    parsed = _queue_parser_module().parse_queue(text)
    if parsed.errors:
        return False
    selected = next((row for row in parsed.rows if row.line == line_number), None)
    if selected is None or selected.mark.lower() != expected_mark.lower():
        return False
    if expected_row is not None:
        # Repair may deliberately change the old selector. Its evidence gate
        # checked the replacement; require that exact row to survive parsing.
        return selected.raw == expected_row.rstrip("\r\n")
    return _matches_selector(
        selected.raw,
        selector,
        selector_kind,
        prose_allowed=prose_allowed,
        parsed_row=selected,
    )


def regenerate_dive_queue(kb_root: Path, *, strict: bool = False) -> bool:
    """Best-effort refresh of the derived `_dive-queue.md` dashboard.

    Runs as a subprocess after our lock is released (the derive script takes
    the same `.kb-write.lock`; calling it inline while holding the lock would
    deadlock).  The historical default remains best-effort: a stale dashboard
    is preferable to making an ordinary check-off fail.  Narrow workflows
    that must publish a truthful completion state may pass ``strict=True``;
    then a non-zero exit, timeout, or launch failure raises and the caller can
    retain its own partial checkpoint.
    """
    script = Path(__file__).resolve().parent / "kb-derive-dive-queue.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--kb-root", str(kb_root), "--write"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as exc:
        if strict:
            raise RuntimeError(f"dive-queue regeneration failed: {exc}") from exc
        print(f"warning: dive-queue regen failed: {exc}", file=sys.stderr)
        return False

    if completed.returncode != 0:
        details: list[str] = []
        for stream_name in ("stderr", "stdout"):
            stream = getattr(completed, stream_name, b"")
            if isinstance(stream, bytes):
                stream = stream.decode("utf-8", errors="replace")
            if stream:
                details.append(str(stream).strip()[:1000])
        suffix = ": " + " | ".join(item for item in details if item) if details else ""
        error = RuntimeError(
            f"dive-queue regeneration exited {completed.returncode}{suffix}"
        )
        if strict:
            raise error
        print(f"warning: {error}", file=sys.stderr)
        return False
    return True


def append_ideas_backlog(backlog_path: Path, routed_body: str, source_file: Path, source_line: int) -> None:
    today = dt.date.today().isoformat()
    rel = source_file
    entry = (
        f"- [ ] {routed_body} — routed from `{rel}` line {source_line} on {today}\n"
    )
    current = backlog_path.read_text(encoding="utf-8")
    if entry not in current:
        atomic_write(backlog_path, current.rstrip() + "\n" + entry)


INGEST_NOTE_RE = re.compile(
    r"\s+—\s+ingested\s+\d{4}-\d{2}-\d{2}\s+(?:→|->)\s+\[\[[^\]]+\]\].*$"
)
SOURCE_RECEIPT_RE = re.compile(r"\s+—\s+validated-source\s+\[\[[^\]]+\]\]\s+sha256:[0-9a-f]{64}(?:\s+warnings-accepted:.*)?$")
REVIEW_RECEIPT_RE = re.compile(r"\s+—\s+review-receipt\s+sha256:[0-9a-f]{64}$")


def source_evidence(file_path, kb_root, row, note, source_note=None, accept_warnings="", review_path=None):
    relative = file_path.resolve().relative_to(kb_root)
    if len(relative.parts) != 3 or relative.parts[0] != "topics":
        return ""
    module = _source_completion_module()
    locator = _primary_locator(row)
    if locator is None:
        raise ValueError("topic ingestion checkoff requires a URL-first or absolute local-PDF row")
    return module.completion_receipt(kb_root, locator, note, source_note, accept_warnings,
                                     review_path=review_path, queue_path=relative.as_posix())


def checkoff(
    file_path: Path,
    url: str,
    mark: str,
    note: str,
    lock_path: Path,
    ideas_backlog: Path | None = None,
    kb_root: Path | None = None,
    from_mark: str = " ",
    strip_ingest_note: bool = False,
    source_note: str | None = None,
    accept_source_warnings: str = "",
    required_sections: set[str] | None = None,
    review_path: Path | str | None = None,
) -> tuple[bool, str]:
    if not all(isinstance(value, str) for value in (url, note, accept_source_warnings)):
        return False, "queue arguments must be text"
    if any("\n" in value or "\r" in value for value in (url, note, accept_source_warnings)):
        return False, "queue arguments must be single-line"
    try:
        selector_kind = _selector_kind(url)
        mark = normalize_mark(mark)
        from_mark = normalize_mark(from_mark)
    except ValueError as exc:
        return False, f"invalid queue selector or mark: {exc}"
    try:
        kb_root = _write_guard.canonical_root(kb_root or lock_path.parent)
    except (OSError, ValueError) as exc:
        return False, f"invalid KB root: {exc}"
    try:
        with _write_guard.write_lock(kb_root, lock_path):
            try:
                text = guarded_text(file_path, kb_root)
                if ideas_backlog is not None:
                    guarded_text(ideas_backlog, kb_root)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                return False, f"unsafe queue target: {exc}"
            lines = text.splitlines(keepends=True)
            parsed = _queue_parser_module().parse_queue(text)
            parsed_rows = {row.line: row for row in parsed.rows}
            prose_allowed = _prose_selector_allowed(file_path, kb_root)
            hits = []
            for i, line in enumerate(lines):
                parsed_row = parsed_rows.get(i + 1)
                match = CHECKBOX_RE.match(line)
                if (
                    parsed_row is not None
                    and match
                    and parsed_row.mark.lower() == from_mark
                    and _matches_selector(
                        line,
                        url,
                        selector_kind,
                        prose_allowed=prose_allowed,
                        parsed_row=parsed_row,
                    )
                ):
                    hits.append(i)
            if not hits:
                return False, f"no [{from_mark}] line matching selector: {url}"
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} [{from_mark}] lines match selector: {url}"
            i = hits[0]
            selected_row = parsed_rows.get(i + 1)
            line = lines[i]
            allowed_sections = required_sections
            relative = _write_guard.canonical_queue_path(file_path, kb_root).relative_to(kb_root)
            if mark == "x" and from_mark != "x" and relative.parts[0] == "topics":
                approved_sections = {"Approved", "Auto-approved"}
                allowed_sections = (approved_sections if allowed_sections is None
                                    else approved_sections & allowed_sections)
            if allowed_sections is not None:
                section = selected_row.section if selected_row is not None else None
                if mark == "x" and section in {"Approved", "Auto-approved"}:
                    try:
                        parsed.require_approval_safe()
                    except ValueError as exc:
                        return False, f"queue approval structure is not permitted: {exc}"
                if section not in allowed_sections:
                    return False, "queue approval section changed or is not permitted"
            if mark == "x":
                try:
                    receipt = source_evidence(file_path, kb_root, line, note, source_note, accept_source_warnings, review_path)
                except (OSError, ValueError) as exc:
                    return False, f"completion refused: {exc}"
                if receipt:
                    note = (note + " — " if note else "") + receipt
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            routed_body = CHECKBOX_RE.sub("", body, count=1).strip()
            body = CHECKBOX_RE.sub(rf"\g<1>{mark}\g<3>", body, count=1).rstrip()
            if strip_ingest_note:
                body = REVIEW_RECEIPT_RE.sub("", body).rstrip()
                body = SOURCE_RECEIPT_RE.sub("", body).rstrip()
                body = INGEST_NOTE_RE.sub("", body).rstrip()
            if note:
                body = f"{body} — {note}"
            lines[i] = body + newline
            if not _proposed_queue_ok(
                "".join(lines),
                i + 1,
                url,
                selector_kind,
                prose_allowed=prose_allowed,
                expected_mark=mark,
            ):
                return False, "queue structure changed; refusing unsafe checkoff"
            if ideas_backlog is not None:
                source_ref = file_path
                if kb_root is not None:
                    try:
                        source_ref = file_path.relative_to(kb_root)
                    except ValueError:
                        pass
                append_ideas_backlog(ideas_backlog, routed_body, source_ref, i + 1)
            atomic_write(file_path, "".join(lines))
            return True, f"marked [{mark}] line {i + 1} in {file_path.name}"
    except _write_guard.WriteGuardError as exc:
        return False, f"unsafe KB lock: {exc}"


def replace_line_text(
    file_path: Path,
    url: str,
    old: str,
    new: str,
    lock_path: Path,
    from_mark: str | None = None,
    review_path: Path | str | None = None,
) -> tuple[bool, str]:
    if not all(isinstance(value, str) for value in (url, old, new)):
        return False, "queue repair arguments must be text"
    if any("\n" in value or "\r" in value for value in (url, old, new)):
        return False, "queue repair arguments must be single-line"
    try:
        selector_kind = _selector_kind(url)
        normalized_from_mark = normalize_mark(from_mark) if from_mark is not None else None
    except ValueError as exc:
        return False, f"invalid queue selector or mark: {exc}"
    try:
        kb_root = _write_guard.canonical_root(lock_path.parent)
    except (OSError, ValueError) as exc:
        return False, f"invalid KB root: {exc}"
    try:
        with _write_guard.write_lock(kb_root, lock_path):
            try:
                text = guarded_text(file_path, kb_root)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                return False, f"unsafe queue target: {exc}"
            lines = text.splitlines(keepends=True)
            parsed = _queue_parser_module().parse_queue(text)
            parsed_rows = {row.line: row for row in parsed.rows}
            prose_allowed = _prose_selector_allowed(file_path, kb_root)
            hits = []
            for i, line in enumerate(lines):
                parsed_row = parsed_rows.get(i + 1)
                match = CHECKBOX_RE.match(line)
                if (
                    parsed_row is None
                    or not match
                    or not _matches_selector(
                        line,
                        url,
                        selector_kind,
                        prose_allowed=prose_allowed,
                        parsed_row=parsed_row,
                    )
                ):
                    continue
                if from_mark is not None and parsed_row.mark.lower() != normalized_from_mark:
                    continue
                hits.append(i)
            if not hits:
                mark_label = f" [{from_mark}]" if from_mark is not None else ""
                return False, f"no{mark_label} line matching selector: {url}"
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} lines match selector: {url}"
            i = hits[0]
            selected_row = parsed_rows.get(i + 1)
            if old not in lines[i]:
                return False, f"line {i + 1} does not contain replacement text"
            replacement = lines[i].replace(old, new)
            original_mark = CHECKBOX_RE.match(lines[i])
            replacement_mark = CHECKBOX_RE.match(replacement)
            if not replacement_mark or replacement_mark.group(2) != original_mark.group(2):
                return False, "text repair cannot change checkbox state; use the mark operation"
            if replacement_mark.group(2).lower() == "x":
                selected_relative = _write_guard.canonical_queue_path(file_path, kb_root).relative_to(kb_root)
                if selected_relative.parts[0] == "topics":
                    if selected_row is None or selected_row.section is None:
                        return False, "queue approval section changed or is not permitted"
                    if selected_row.section in {"Approved", "Auto-approved"}:
                        try:
                            parsed.require_approval_safe()
                        except ValueError as exc:
                            return False, f"queue approval structure is not permitted: {exc}"
                try:
                    receipt = source_evidence(file_path, kb_root, replacement, replacement.rstrip("\r\n"), review_path=review_path)
                except (OSError, ValueError) as exc:
                    return False, f"completion refused: {exc}"
                if receipt and receipt not in replacement:
                    replacement = replacement.rstrip() + " — " + receipt + "\n"
            lines[i] = replacement
            if not _proposed_queue_ok(
                "".join(lines),
                i + 1,
                url,
                selector_kind,
                prose_allowed=prose_allowed,
                expected_mark=replacement_mark.group(2),
                expected_row=replacement,
            ):
                return False, "queue structure changed; refusing unsafe repair"
            atomic_write(file_path, "".join(lines))
            return True, f"replaced text on line {i + 1} in {file_path.name}"
    except _write_guard.WriteGuardError as exc:
        return False, f"unsafe KB lock: {exc}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Race-safe queue-line check-off.")
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--file", required=True, help="queue file (relative to kb-root or absolute)")
    p.add_argument(
        "--url",
        required=True,
        help="exact primary HTTP(S)/absolute-PDF locator, or a unique prose selector in an inbox/backlog row",
    )
    p.add_argument("--from-mark", choices=[" ", "x", "X", "!", "~", "?"], default=" ")
    p.add_argument("--mark", choices=["x", "!", "~", "?"], default="x")
    p.add_argument("--note", default="")
    p.add_argument("--source-note", help="Existing source note backing a completed topic URL/PDF row")
    p.add_argument("--accept-source-warnings", default="", help="Explicit reason to accept non-blocking source warnings")
    p.add_argument("--review-receipt", type=Path, help="Canonical immutable review record required for topic source completion")
    p.add_argument(
        "--replace-text",
        help="repair-only: exact text to replace within the unique matched queue line",
    )
    p.add_argument(
        "--with-text",
        default="",
        help="replacement text used with --replace-text",
    )
    p.add_argument(
        "--repair-uncheck",
        action="store_true",
        help=(
            "repair-only: mark a previously completed line back to [ ], stripping "
            "the standard trailing 'ingested YYYY-MM-DD -> [[source]]' note"
        ),
    )
    p.add_argument(
        "--route-ideas-backlog",
        action="store_true",
        help="also append the original checked-off item to ideas/_backlog.md under the same lock",
    )
    args = p.parse_args(argv)
    mark = args.mark
    from_mark = args.from_mark
    strip_ingest_note = False
    if args.repair_uncheck:
        mark = " "
        strip_ingest_note = True
        if from_mark == " ":
            from_mark = "x"
    # Preserve the lexical root so the shared guard can reject symlinked roots
    # and parent traversal rather than silently following them.
    kb_root = Path(args.kb_root).expanduser().absolute()
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
    if args.replace_text is not None:
        ok, msg = replace_line_text(
            fp,
            args.url,
            args.replace_text,
            args.with_text,
            kb_root / LOCK_NAME,
            from_mark=args.from_mark if args.from_mark != " " else None,
            review_path=args.review_receipt,
        )
        print(msg, file=sys.stderr if not ok else sys.stdout)
        if ok:
            regenerate_dive_queue(kb_root)
        return 0 if ok else 1
    ok, msg = checkoff(
        fp,
        args.url,
        mark,
        args.note,
        kb_root / LOCK_NAME,
        ideas_backlog=ideas_backlog,
        kb_root=kb_root,
        from_mark=from_mark,
        strip_ingest_note=strip_ingest_note,
        source_note=args.source_note,
        accept_source_warnings=args.accept_source_warnings,
        review_path=args.review_receipt,
    )
    print(msg, file=sys.stderr if not ok else sys.stdout)
    if ok:
        regenerate_dive_queue(kb_root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
