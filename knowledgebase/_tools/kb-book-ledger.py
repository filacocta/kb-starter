#!/usr/bin/env python3
"""Race-safe mutation of the book ledger book acquisition ledger.

The ledger is a mutable KB control file, not a free-form scratch file.  This
tool validates its canonical path and ``author: kb-bot`` frontmatter while
holding the shared writer lock, then changes only the selected entry (plus the
frontmatter ``modified`` date).  Entry lookup intentionally keeps the existing
normalized exact-or-substring title semantics: short title selectors are useful
for interactive ledger maintenance and are not URL locators.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Iterator

import yaml

LOCK_NAME = ".kb-write.lock"
LEDGER_RELATIVE = Path("_library") / "books" / "_ledger.md"
SECTIONS = ["Wanted", "Proposed", "Acquired", "Ingested"]
FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^##[ \t]+(.+?)\s*$")
ENTRY_RE = re.compile(r"^\s*-\s+\[[ xX!~?]\]\s+\*\*(?P<title>.+?)\*\*")
URL_RE = re.compile(r"https?://[^\s)\]]+")


def _guard_module():
    """Load the shared filesystem guard despite the hyphenated filename."""
    key = "kb_book_ledger_write_guard"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-write-guard.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load kb-write-guard.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def _single_line(name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be single-line")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")


def default_ledger(kb_root: Path) -> Path:
    return kb_root / LEDGER_RELATIVE


def norm_title(t: str) -> str:
    """Normalize a title for the historical exact-or-substring selector."""
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _canonical_root(kb_root: Path) -> Path:
    return _guard_module().canonical_root(kb_root)


def _canonical_ledger(root: Path, path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if candidate.absolute() != resolved or candidate.is_symlink():
        raise ValueError("ledger path must be a canonical non-symlink path")
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("ledger path must be inside the KB root") from exc
    if relative != LEDGER_RELATIVE:
        raise ValueError("ledger path must be _library/books/_ledger.md")
    if not resolved.is_file():
        raise ValueError("ledger path is not a regular file")
    return resolved


def _guarded_text(path: Path, root: Path) -> str:
    resolved = path.resolve(strict=True)
    if path.absolute() != resolved or path.is_symlink():
        raise ValueError("ledger path must be a canonical non-symlink path")
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("ledger path must be inside the KB root") from exc
    if relative != LEDGER_RELATIVE or not resolved.is_file():
        raise ValueError("target is not the canonical book ledger")
    # Use the shared checked descriptor so a ledger read never follows a
    # replaced symlink or a multiply-linked file.
    text = _guard_module().regular_text(resolved)
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path} lacks YAML frontmatter")
    try:
        # Reuse the shared duplicate-key-safe YAML loader.  The helper expects
        # LF delimiters, so normalize only this in-memory frontmatter copy;
        # the original ledger bytes remain untouched until the guarded write.
        metadata = _guard_module().frontmatter_metadata(text.replace("\r\n", "\n"), resolved)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"{path} has invalid YAML frontmatter") from exc
    if not isinstance(metadata, dict) or metadata.get("author") != "kb-bot":
        raise ValueError(f"{path} is not kb-bot authored; refusing to write")
    return text


@contextlib.contextmanager
def _write_lock(kb_root: Path, lock_path: Path) -> Iterator[Path]:
    root = _canonical_root(kb_root)
    expected = root / LOCK_NAME
    candidate = lock_path.expanduser()
    absolute = candidate.absolute() if candidate.is_absolute() else (root / candidate).absolute()
    if absolute != expected:
        raise ValueError("invalid KB lock path")
    with _guard_module().write_lock(root, expected):
        yield root


def parse(text: str):
    """Compatibility parser returning preamble and section entry blocks.

    This public helper retains the original ledger API.  Mutations use the
    line-span parser below so unrelated prose and spacing are not re-rendered.
    """
    lines = text.splitlines()
    sections: dict[str, list[list[str]]] = {s: [] for s in SECTIONS}
    section_order: list[str] = []
    preamble: list[str] = []
    cur_section = None
    cur_entry: list[str] | None = None

    def flush_entry():
        nonlocal cur_entry
        if cur_entry is not None and cur_section in sections:
            sections[cur_section].append(cur_entry)
        cur_entry = None

    for line in lines:
        m = HEADING_RE.match(line)
        if m and m.group(1) in sections:
            flush_entry()
            cur_section = m.group(1)
            section_order.append(cur_section)
            continue
        if cur_section is None:
            preamble.append(line)
            continue
        if ENTRY_RE.match(line):
            flush_entry()
            cur_entry = [line]
        elif cur_entry is not None and (line.strip() == "" or line.startswith((" ", "\t"))):
            cur_entry.append(line)
        else:
            flush_entry()
            sections.setdefault("_chrome_" + cur_section, []).append([line])
    flush_entry()
    return preamble, sections


def entry_title(block: list[str]) -> str:
    m = ENTRY_RE.match(block[0])
    return m.group("title").strip() if m else ""


def render(preamble: list[str], sections: dict[str, list[list[str]]]) -> str:
    """Render the legacy parse representation for callers that use it directly."""
    out: list[str] = []
    out.extend(preamble)
    if out and out[-1].strip() != "":
        out.append("")
    for section in SECTIONS:
        out.append(f"## {section}")
        out.append("")
        for chrome in sections.get("_chrome_" + section, []):
            out.extend(chrome)
        for block in sections[section]:
            body = list(block)
            while body and body[-1].strip() == "":
                body.pop()
            out.extend(body)
        out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).rstrip() + "\n")


def find(sections, title) -> tuple[str | None, int]:
    """Find the first exact normalized or normalized-substring title match."""
    nt = norm_title(title)
    for section in SECTIONS:
        for index, block in enumerate(sections[section]):
            candidate = norm_title(entry_title(block))
            if candidate == nt or (nt and nt in candidate):
                return section, index
    return None, -1


def touch_modified(preamble: list[str]) -> None:
    today = dt.date.today().isoformat()
    for i, line in enumerate(preamble):
        if line.startswith("modified:"):
            ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            preamble[i] = f"modified: {today}{ending}"
            return


class _EntrySpan:
    __slots__ = ("section", "start", "end")

    def __init__(self, section: str, start: int, end: int) -> None:
        self.section = section
        self.start = start
        self.end = end


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    heading = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        if heading is None and match.group(1) == section:
            heading = index
            continue
        if heading is not None:
            end = index
            break
        # An earlier noncanonical/canonical H2 does not affect this section.
    return (heading, end) if heading is not None else None


def _entry_spans(lines: list[str]) -> list[_EntrySpan]:
    spans: list[_EntrySpan] = []
    current: str | None = None
    current_end = len(lines)
    for index, line in enumerate(lines):
        heading = HEADING_RE.match(line.rstrip("\r\n"))
        if heading:
            if heading.group(1) in SECTIONS:
                current = heading.group(1)
                current_end = len(lines)
            else:
                current = None
            continue
        if current is None or index >= current_end or not ENTRY_RE.match(line):
            continue
        start = index
        end = index + 1
        while end < len(lines):
            if HEADING_RE.match(lines[end].rstrip("\r\n")) or ENTRY_RE.match(lines[end]):
                break
            if lines[end].strip() == "" or lines[end].startswith((" ", "\t")):
                end += 1
                continue
            break
        spans.append(_EntrySpan(current, start, end))
    # A heading scan above does not know each section's next H2 until after the
    # fact; discard a defensive span that crossed it.
    return [span for span in spans if span.end <= (_section_bounds(lines, span.section) or (0, 0))[1]]


def _find_span(lines: list[str], title: str) -> _EntrySpan | None:
    wanted = norm_title(title)
    for span in _entry_spans(lines):
        candidate = norm_title(entry_title(lines[span.start:span.end]))
        if candidate == wanted or (wanted and wanted in candidate):
            return span
    return None


def _line_ending(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _without_trailing_blanks(block: list[str]) -> list[str]:
    result = list(block)
    while result and result[-1].strip() == "":
        result.pop()
    return result


def _with_line_ending(line: str, ending: str) -> str:
    return line if line.endswith(("\n", "\r")) else line + ending


def _insert_block(lines: list[str], section: str, block: list[str], ending: str) -> None:
    bounds = _section_bounds(lines, section)
    if bounds is None:
        raise ValueError(f"ledger section '## {section}' not found")
    head, end = bounds
    insert = end
    while insert > head + 1 and lines[insert - 1].strip() == "":
        insert -= 1
    block = [_with_line_ending(line, ending) for line in _without_trailing_blanks(block)]
    separator: list[str] = []
    if insert == head + 1 or (insert > head + 1 and lines[insert - 1].strip() != ""):
        separator.append(ending)
    lines[insert:insert] = separator + block + [ending]


def _append_continuation(lines: list[str], span: _EntrySpan, content: str, ending: str) -> None:
    index = span.end
    while index > span.start and lines[index - 1].strip() == "":
        index -= 1
    lines.insert(index, _with_line_ending("      " + content, ending))


def _update_modified(lines: list[str], today: str) -> None:
    match = FRONTMATTER_RE.match("".join(lines))
    if not match:
        raise ValueError("ledger lacks YAML frontmatter")
    # Locate the first frontmatter line without changing any body prose.
    closing = None
    for index, line in enumerate(lines):
        if index == 0:
            continue
        if line.rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise ValueError("ledger lacks YAML closing fence")
    for index in range(1, closing):
        if lines[index].startswith("modified:"):
            ending = "\r\n" if lines[index].endswith("\r\n") else ("\n" if lines[index].endswith("\n") else "")
            lines[index] = f"modified: {today}{ending}"
            return
    ending = _line_ending(lines)
    lines.insert(closing, f"modified: {today}{ending}")


def _atomic_write(path: Path, text: str) -> None:
    return _guard_module().atomic_write(path, text, prefix=".kb-ledger-")


def _list_data(text: str) -> dict[str, list[dict[str, object]]]:
    _preamble, sections = parse(text)
    data: dict[str, list[dict[str, object]]] = {}
    for section in SECTIONS:
        data[section] = []
        for block in sections[section]:
            title = entry_title(block)
            if not title:
                continue
            urls = URL_RE.findall("\n".join(block))
            data[section].append({"title": title, "urls": urls, "raw": "\n".join(block).strip()})
    return data


def _validate_url(value: str) -> None:
    _single_line("URL", value)
    if not re.fullmatch(r"https?://[^\s]+", value, re.IGNORECASE):
        raise ValueError("URL must be a single HTTP(S) URL")


def _validate_mutation_args(args: argparse.Namespace) -> None:
    _single_line("title", args.title or "")
    if not args.title or not norm_title(args.title):
        raise ValueError("title must not be empty")
    for name in ("author", "year", "referenced_by", "note"):
        _single_line(name.replace("_", " "), getattr(args, name) or "")
    for url in args.url:
        _validate_url(url)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Race-safe mutation of the book ledger book acquisition ledger.")
    ap.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--file", help="ledger path (defaults to _library/books/_ledger.md)")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--move", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--title")
    ap.add_argument("--author", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--referenced-by", dest="referenced_by", default="")
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--to", choices=SECTIONS)
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)

    try:
        root = _canonical_root(Path(args.kb_root))
        supplied = Path(args.file) if args.file else default_ledger(root)
        ledger = _canonical_ledger(root, supplied)
        text = _guarded_text(ledger, root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        data = _list_data(text)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for section in SECTIONS:
                print(f"## {section} ({len(data[section])})")
                for entry in data[section]:
                    urls = entry["urls"]
                    print(f"  - {entry['title']}" + (f"  [{len(urls)} url]" if urls else ""))
        return 0

    actions = [args.add, args.propose, args.approve, args.move]
    if sum(actions) != 1:
        print("error: choose exactly one of --add, --propose, --approve, or --move", file=sys.stderr)
        return 2
    if not args.title:
        print("error: --title is required for mutations", file=sys.stderr)
        return 2
    try:
        _validate_mutation_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.propose and not args.url:
        print("error: --propose requires at least one --url", file=sys.stderr)
        return 2
    if args.approve and len(args.url) != 1:
        print("error: --approve requires exactly one --url (single approved source)", file=sys.stderr)
        return 2
    if args.move and not args.to:
        print("error: --move requires --to", file=sys.stderr)
        return 2

    try:
        with _write_lock(root, root / LOCK_NAME):
            # Re-read and re-check ownership inside the lock; the preflight read
            # above is only for early errors/listing and is not trusted for a
            # mutation.
            text = _guarded_text(ledger, root)
            lines = text.splitlines(keepends=True)
            ending = _line_ending(lines)
            span = _find_span(lines, args.title)
            today = dt.date.today().isoformat()

            if args.add:
                if span is not None:
                    if args.referenced_by:
                        _append_continuation(lines, span, f"Also referenced by {args.referenced_by}", ending)
                    message = f"already tracked in ## {span.section}; appended referencing source"
                else:
                    meta: list[str] = []
                    if args.author:
                        meta.append(args.author)
                    if args.year:
                        meta.append(str(args.year))
                    head = f"- [ ] **{args.title}**"
                    if meta:
                        head += " — " + ", ".join(meta) + "."
                    block = [head]
                    if args.referenced_by:
                        block.append(f"      Referenced by {args.referenced_by}.")
                    _insert_block(lines, "Wanted", block, ending)
                    message = "added to ## Wanted"

            elif args.propose:
                if span is None:
                    raise ValueError(f"{args.title!r} not found")
                block = _without_trailing_blanks(lines[span.start:span.end])
                links = " · ".join(f"[source]({url})" for url in args.url)
                block.append(f"      Proposed: {links}")
                del lines[span.start:span.end]
                _insert_block(lines, "Proposed", block, ending)
                message = f"moved to ## Proposed with {len(args.url)} candidate url(s)"

            elif args.approve:
                if span is None:
                    raise ValueError(f"{args.title!r} not found")
                block = _without_trailing_blanks(lines[span.start:span.end])
                block.append(f"      Approved source ({today}): {args.url[0]}")
                del lines[span.start:span.end]
                _insert_block(lines, "Acquired", block, ending)
                message = "moved to ## Acquired; recorded approved source"

            else:  # --move
                if span is None:
                    raise ValueError(f"{args.title!r} not found")
                block = _without_trailing_blanks(lines[span.start:span.end])
                if args.note:
                    block.append(f"      {args.note} ({today})")
                del lines[span.start:span.end]
                _insert_block(lines, args.to, block, ending)
                message = f"moved {span.section} -> {args.to}"

            _update_modified(lines, today)
            _atomic_write(ledger, "".join(lines))
            print(f"{args.title}: {message}")
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
