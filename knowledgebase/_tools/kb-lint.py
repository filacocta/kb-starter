#!/usr/bin/env python3
"""Structural lint checks for a knowledge base."""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


TYPE_VALUES = {
    "source",
    "concept",
    "entity",
    "media-description",
    "synthesis",
    "output",
    "summary",
    "index",
    "prospects",
    "catalog",
    "todos",
    "research-ideas",
    "meta",
}

TITLE_TYPES = {
    "source",
    "concept",
    "entity",
    "media-description",
    "synthesis",
    "output",
    "summary",
    "prospects",
    "catalog",
    "todos",
    "research-ideas",
}

STATUS_TYPES = {"source", "concept", "entity", "media-description", "synthesis", "output", "prospects"}
TOPIC_TYPES = {"source", "concept", "entity", "media-description", "synthesis", "output", "summary", "prospects", "research-ideas"}
SKIP_DIRS = {"clippings", "readinglist", "_media", "_tools", "__pycache__", ".pytest_cache", ".git", ".codex", ".agents"}
VALID_LINK_ROOTS = {"knowledgebase", "topics", "notes", "sources", "projects"}
WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
LEGACY_SOURCE_TARGET_RE = re.compile(r"(?:→|->)\s+(sources/[A-Za-z0-9._/-]+)")
COMPLETED_SOURCE_WIKILINK_RE = re.compile(r"(?:→|->)\s+\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

# Canonical reading-list section ladder (cross-agent contract; see _schema.md "Topic Reading Lists").
CANONICAL_READING_SECTIONS = ("Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry")

# Files every project entry under projects/<slug>/ must carry (see _schema.md project layout).
PROJECT_REQUIRED_FILES = ("_summary.md", "_todos.md")
# Default staleness window (days). An `status: active` project whose _summary hasn't been
# touched in this long — or whose backing code repo has commits this far past the _summary's
# `modified` date — is flagged so kb-project wrap gets run. Tune with --stale-days.
DEFAULT_STALE_DAYS = 21
# The summary format describes navigation summaries as "under 300 words". Keep the
# boundary explicit so callers/tests do not have to duplicate the policy.
SUMMARY_WORD_LIMIT = 300
SUMMARY_TOO_LONG_CODE = "topic-summary-too-long"

# Task-board headings have historically appeared with or without the emoji and with either
# ``Testing/Review`` or ``Testing / Review``.  These aliases are intentionally
# advisory-compatible rather than a request to normalize existing boards.
PROJECT_TODO_SECTION_PATTERNS = {
    "backlog": re.compile(r"^(?:🗂️\s*)?backlog(?:\s|$)", re.IGNORECASE),
    "in-progress": re.compile(r"^(?:🔄\s*)?in\s+progress(?:\s|$)", re.IGNORECASE),
    "testing-review": re.compile(r"^(?:🧪\s*)?testing\s*/\s*review(?:\s|$)", re.IGNORECASE),
    "done": re.compile(r"^(?:✅\s*)?done(?:\s|$)", re.IGNORECASE),
}
PROJECT_TODO_CHECKBOX_RE = re.compile(r"^ {0,3}[-*]\s*\[([^\]])\](?:\s*(.*))?$")
PROJECT_TODO_ALLOWED_MARKERS = {" ", "~", "?", "x", "X", "!"}


def _load_queue_parser():
    """Load the shared pure queue parser despite its hyphenated filename."""
    key = "kb_lint_queue_parser"
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


def _load_project_status():
    """Load the shared project metadata/status primitives."""
    key = "kb_lint_project_status"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).with_name("kb-project-status.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-project-status.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def rel(path: Path, kb_root: Path) -> str:
    return path.relative_to(kb_root).as_posix()


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line or line.startswith((" ", "-")):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, body


def is_library_payload(path: Path, kb_root: Path) -> bool:
    """Raw extraction/custody payload under `_library/{books,docs}/` — the text
    equivalent of the binary custody files (e.g. `*.ocr.md` OCR dumps of book PDFs).
    Like the binaries (library custody), these are exempt from kb-bot frontmatter: they are
    raw source content, not notes or control files. Only `_`-prefixed Markdown
    CONTROL files in `_library/` (e.g. `_README.md`, `_ledger.md`) require frontmatter,
    matching the KB convention that system files are `_`-prefixed."""
    try:
        parts = path.relative_to(kb_root).parts
    except ValueError:
        return False
    if len(parts) >= 2 and parts[0] == "_library" and parts[1] in ("books", "docs"):
        return not path.name.startswith("_")
    return False


def iter_md_files(kb_root: Path, topic: str | None) -> list[Path]:
    if topic:
        roots = [kb_root / "topics" / topic]
    else:
        roots = [kb_root]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            parts = set(path.relative_to(kb_root).parts)
            if parts & SKIP_DIRS:
                continue
            if is_library_payload(path, kb_root):
                continue
            files.append(path)
    return sorted(files)


def iter_media_files(kb_root: Path) -> list[Path]:
    media_root = kb_root / "_media"
    if not media_root.exists():
        return []
    return sorted(p for p in media_root.rglob("*") if p.is_file() and p.name != "_README.md")


def add(findings: list[dict[str, Any]], severity: str, code: str, path: str, message: str, line: int | None = None) -> None:
    item: dict[str, Any] = {"severity": severity, "code": code, "path": path, "message": message}
    if line is not None:
        item["line"] = line
    findings.append(item)


def summary_scope(path: Path, kb_root: Path) -> str | None:
    """Return the canonical summary scope for a topic/project summary path."""
    parts = path.relative_to(kb_root).parts
    if len(parts) != 3 or parts[2] != "_summary.md":
        return None
    if parts[0] == "topics":
        return "topic"
    if parts[0] == "projects":
        return "project"
    return None


def check_summary_length(
    kb_root: Path,
    path: Path,
    body: str,
    findings: list[dict[str, Any]],
) -> None:
    """Warn when a canonical topic/project navigation body reaches 300 words.

    Frontmatter is passed separately by ``check_frontmatter`` and therefore is
    never included in this whitespace-delimited count.  This helper is the
    sole producer of the summary length finding so ``--all`` cannot report a
    project summary twice through the project-staleness pass.
    """
    scope = summary_scope(path, kb_root)
    if scope is None:
        return
    words = len(body.split())
    if words < SUMMARY_WORD_LIMIT:
        return
    add(
        findings,
        "warning",
        SUMMARY_TOO_LONG_CODE,
        rel(path, kb_root),
        (
            f"{scope} summary body has {words} whitespace-delimited words; "
            f"summary format requires under {SUMMARY_WORD_LIMIT} (frontmatter excluded); "
            "preserve useful detail in linked notes before shortening"
        ),
    )


def check_authorship(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        if fm.get("author") != "kb-bot":
            add(findings, "error", "authorship", rel(path, kb_root), "missing author: kb-bot frontmatter")


def check_frontmatter(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        file_rel = rel(path, kb_root)
        check_summary_length(kb_root, path, body, findings)
        missing = [field for field in ("author", "created", "modified", "type") if not fm.get(field)]
        note_type = fm.get("type", "")
        if note_type and note_type not in TYPE_VALUES:
            add(findings, "error", "frontmatter", file_rel, f"invalid type: {note_type}")
        if note_type in TOPIC_TYPES and not fm.get("topic"):
            missing.append("topic")
        if note_type in TITLE_TYPES:
            if not fm.get("title"):
                missing.append("title")
            if not fm.get("description"):
                missing.append("description")
        if note_type in STATUS_TYPES and not fm.get("status"):
            if note_type == "source":
                add(findings, "info", "legacy-source-frontmatter", file_rel, "source note lacks status; body is immutable, so treat as legacy metadata drift")
            else:
                missing.append("status")
        if note_type == "media-description":
            for field in ("media_id", "media_kind", "media_status"):
                if not fm.get(field):
                    missing.append(field)
            status = fm.get("media_status")
            if status == "captured" and not fm.get("local_path"):
                missing.append("local_path")
            if status in {"discovered", "not-captured"} and not fm.get("origin_url"):
                missing.append("origin_url")
        if note_type == "source":
            origin_urls = URL_RE.findall(fm.get("origin", ""))
            if len(origin_urls) > 1:
                add(
                    findings,
                    "warning",
                    "source-origin-multiple-urls",
                    file_rel,
                    (
                        "source note origin contains multiple URLs; independent fetched URLs "
                        "should normally be split into one source note per source"
                    ),
                )
        if missing:
            add(findings, "error", "frontmatter", file_rel, "missing fields: " + ", ".join(sorted(set(missing))))


def wikilinks_outside_code(text: str) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in WIKILINK_RE.finditer(line):
            links.append((lineno, match.group(1).strip()))
    return links


def resolve_link(kb_root: Path, target: str) -> Path:
    path = Path(target)
    raw = kb_root.parent / path
    if raw.is_dir():
        summary = raw / "_summary.md"
        return summary if summary.exists() else raw
    if path.suffix:
        return raw
    return raw.with_suffix(".md")


def check_wikilinks(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        note_type = fm.get("type")
        if note_type == "meta":
            continue
        for lineno, target in wikilinks_outside_code(body):
            if "<" in target or "..." in target:
                continue
            first = target.split("/", 1)[0]
            if first not in VALID_LINK_ROOTS:
                continue
            file_rel = rel(path, kb_root)
            if first != "knowledgebase":
                severity = "info" if note_type == "source" else "warning"
                add(findings, severity, "short-wikilink", file_rel, f"short-form wikilink: [[{target}]]", lineno)
                continue
            resolved = resolve_link(kb_root, target)
            if not resolved.exists():
                add(findings, "error", "broken-wikilink", file_rel, f"missing target: [[{target}]]", lineno)


def frontmatter_values(files: list[Path], key: str) -> list[tuple[Path, str]]:
    values: list[tuple[Path, str]] = []
    for path in files:
        fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        value = fm.get(key)
        if value:
            values.append((path, value))
    return values


def check_media(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    media_files = iter_media_files(kb_root)
    media_notes = sorted(kb_root.glob("topics/*/notes/media/*.md"))
    local_paths = dict(frontmatter_values(media_notes, "local_path"))
    media_ids = frontmatter_values(media_notes, "media_id")
    local_path_values = set(local_paths.values())

    for media_file in media_files:
        vault_path = (Path("knowledgebase") / media_file.relative_to(kb_root)).as_posix()
        if vault_path not in local_path_values:
            add(findings, "warning", "orphan-media", vault_path, "media file has no media-description local_path")

    for note, local_path in local_paths.items():
        if not (kb_root.parent / local_path).exists():
            add(findings, "error", "missing-media-file", rel(note, kb_root), f"local_path does not exist: {local_path}")

    seen: dict[str, Path] = {}
    for note, media_id in media_ids:
        previous = seen.get(media_id)
        if previous:
            add(findings, "warning", "duplicate-media-id", rel(note, kb_root), f"also used by {rel(previous, kb_root)}")
        else:
            seen[media_id] = note

    # Captured image media-description notes must carry `## OCR` and
    # `## Vision Description` sections so kb-ocr-enrich.py can fill them (the
    # tool only fills existing sections, never adds one).
    for note in media_notes:
        fm, body = split_frontmatter(note.read_text(encoding="utf-8"))
        if fm.get("type") != "media-description":
            continue
        if fm.get("media_status") != "captured":
            continue
        if fm.get("media_kind", "image") != "image":
            continue
        if not re.search(r"^##\s+OCR\s*$", body, re.MULTILINE):
            add(findings, "warning", "media-missing-ocr", rel(note, kb_root),
                "captured image media-description note has no '## OCR' section (kb-ocr-enrich.py only fills existing sections)")
        if not re.search(r"^##\s+Vision Description\s*$", body, re.MULTILINE):
            add(findings, "warning", "media-missing-vision-description", rel(note, kb_root),
                "captured image media-description note has no '## Vision Description' section (kb-ocr-enrich.py only fills existing sections)")

    for path in files:
        text = path.read_text(encoding="utf-8")
        for lineno, target in wikilinks_outside_code(text):
            if not target.startswith("knowledgebase/_media/"):
                continue
            if not (kb_root.parent / target).exists():
                add(findings, "error", "missing-media-embed", rel(path, kb_root), f"embed target does not exist: {target}", lineno)


def _load_kb_seen(kb_root: Path) -> Any:
    module_path = kb_root / "_tools" / "kb-seen.py"
    spec = importlib.util.spec_from_file_location("kb_seen", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row_section(parsed: Any, row: Any) -> tuple[str, int]:
    """Return the nearest semantic H2 name and line for a parsed row."""
    if row.section is not None:
        return row.section, row.section_line or row.line
    for heading in reversed(parsed.headings):
        if heading.line < row.line:
            return heading.name, heading.line
    return "(before first heading)", 1


def iter_reading_lists(kb_root: Path, topic: str | None) -> list[Path]:
    if topic:
        return [kb_root / "topics" / topic / "reading-list.md"]
    return sorted(kb_root.glob("topics/*/reading-list.md"))


def source_note_link(source_note: str) -> str:
    target = source_note.removesuffix(".md")
    if not target.startswith("knowledgebase/"):
        target = f"knowledgebase/{target}"
    return f"[[{target}]]"


def stale_global_inbox_cleanup_command(primary_url: str, source_note: str) -> str:
    note = f"already ingested -> {source_note_link(source_note)}"
    parts = [
        "./_tools/kb-checkoff.py",
        "--file",
        "readinglist/Links.md",
        "--url",
        primary_url,
        "--mark",
        "x",
        "--note",
        note,
    ]
    return " ".join(shlex.quote(part) for part in parts)


def check_global_inbox(
    kb_root: Path,
    findings: list[dict[str, Any]],
    kb_seen: Any,
    seen_index: dict[str, str],
) -> None:
    path = kb_root / "readinglist" / "Links.md"
    if not path.exists():
        return
    file_rel = rel(path, kb_root)
    parsed = _load_queue_parser().parse_queue(path.read_text(encoding="utf-8"))
    for row in parsed.rows:
        if row.mark.lower() != " ":
            continue
        primary_url = row.locator
        if not primary_url:
            continue
        source_note = kb_seen.is_ingested(primary_url, kb_root, seen_index)
        if not source_note:
            continue
        cleanup_command = stale_global_inbox_cleanup_command(primary_url, source_note)
        add(
            findings,
            "warning",
            "global-inbox-already-ingested",
            file_rel,
            (
                f"Unchecked global inbox URL already ingested as {source_note_link(source_note)}: "
                f"{primary_url}. Cleanup: {cleanup_command}"
            ),
            row.line,
        )
        findings[-1]["url"] = primary_url
        findings[-1]["source_note"] = source_note
        findings[-1]["cleanup_command"] = cleanup_command


def completed_source_target(line: str, reading_list: Path, kb_root: Path) -> str | None:
    wikilink = COMPLETED_SOURCE_WIKILINK_RE.search(line)
    if wikilink:
        target_path = wikilink.group(1).strip()
        if target_path.startswith("knowledgebase/topics/") and "/sources/" in target_path:
            return target_path.removesuffix(".md")

    legacy = LEGACY_SOURCE_TARGET_RE.search(line)
    if not legacy:
        return None
    try:
        topic = reading_list.parent.name
        return f"knowledgebase/topics/{topic}/{legacy.group(1).rstrip('.,)')}".removesuffix(".md")
    except ValueError:
        return None


def check_reading_lists(kb_root: Path, topic: str | None, findings: list[dict[str, Any]]) -> None:
    kb_seen = _load_kb_seen(kb_root)
    try:
        seen_index = kb_seen.load_index(kb_root)
        aliases = getattr(kb_seen, 'load_alias_registry', lambda root: ())(kb_root)
        alias_keys = {}
        for entry in aliases:
            text = (kb_root / entry.source_note).read_text(encoding='utf-8')
            metadata = kb_seen._strict_frontmatter(text)
            alias_keys[kb_seen.normalize(entry.alias)] = kb_seen.normalize(metadata['origin'])
    except (ValueError, OSError) as exc:
        add(findings, 'error', 'url-alias-registry', '_url-aliases.json', str(exc))
        return

    def identity(url):
        normalized = kb_seen.normalize(url)
        return alias_keys.get(normalized, normalized)

    source_targets: dict[str, list[tuple[str, int, str]]] = {}

    if topic is None:
        check_global_inbox(kb_root, findings, kb_seen, seen_index)

    for path in iter_reading_lists(kb_root, topic):
        if not path.exists():
            continue
        file_rel = rel(path, kb_root)
        parsed = _load_queue_parser().parse_queue(path.read_text(encoding="utf-8"))
        urls_by_norm: dict[str, list[tuple[str, int, str]]] = {}

        # Enforce the canonical section ladder (see _schema.md "Topic Reading Lists").
        # Only these five level-2 headings are permitted in a reading list; sub-grouping
        # uses level-3 (###) headings inside them. Non-ingestion prose goes to research-ideas.md.
        for heading in parsed.headings:
            if not heading.canonical:
                name = heading.name
                add(
                    findings,
                    "warning",
                    "reading-list-noncanonical-section",
                    file_rel,
                    f"Non-canonical reading-list section '## {name}'. Allowed level-2 headings: "
                    f"{', '.join(CANONICAL_READING_SECTIONS)}. Use ### sub-headings inside these, "
                    f"and move prose/research questions to research-ideas.md.",
                    heading.line,
                )

        # A duplicate or malformed queue ladder is lint-worthy structure, not
        # an approval grant.  Continue scanning real rows so the report still
        # exposes duplicate URLs and stale completed targets.
        duplicate_names = set(parsed.duplicate_sections)
        for heading in parsed.headings:
            if heading.name in duplicate_names:
                add(
                    findings,
                    "warning",
                    "reading-list-duplicate-section",
                    file_rel,
                    f"Duplicate canonical reading-list section '## {heading.name}'; "
                    "approval-sensitive writers will reject this structure.",
                    heading.line,
                )
        for error in parsed.errors:
            if error.startswith("noncanonical section heading") or error.startswith("duplicate section heading"):
                continue
            issue_line: int | None = None
            if parsed.frontmatter_error:
                issue_line = 1
            elif parsed.unclosed_fence:
                issue_line = parsed.unclosed_fence[1]
            elif parsed.unclosed_comment:
                issue_line = parsed.unclosed_comment
            elif parsed.unsupported_html_line:
                issue_line = parsed.unsupported_html_line
            add(
                findings,
                "warning",
                "reading-list-parse-error",
                file_rel,
                f"Queue parser diagnostic: {error}; rows inside the affected structure are ignored.",
                issue_line,
            )

        for row in parsed.rows:
            status = row.mark.lower()
            primary_url = row.locator
            if not primary_url:
                continue
            section, _ = _row_section(parsed, row)
            normalized = identity(primary_url)
            urls_by_norm.setdefault(normalized, []).append((primary_url, row.line, section))
            if status == "x":
                target_path = completed_source_target(row.raw, path, kb_root)
                if target_path:
                    source_targets.setdefault(target_path, []).append((file_rel, row.line, primary_url))
            # A lowercase/otherwise malformed heading is reported above but
            # is not an active approval section.  Keep lint's structural
            # inventory useful without allowing an ambiguous row to trigger
            # approval-specific findings.
            if row.section == "Auto-approved" and status != "x":
                source_note = kb_seen.is_ingested(primary_url, kb_root, seen_index)
                if source_note:
                    add(
                        findings,
                        "warning",
                        "auto-approved-already-ingested",
                        file_rel,
                        f"Auto-approved URL already ingested as {source_note}: {primary_url}",
                        row.line,
                    )

        for normalized, hits in sorted(urls_by_norm.items()):
            sections = {section for _, _, section in hits}
            if len(sections) <= 1:
                continue
            first_url, first_line, first_section = hits[0]
            details = "; ".join(f"line {line} in ## {section}" for _, line, section in hits)
            add(
                findings,
                "warning",
                "reading-list-duplicate-url-across-sections",
                file_rel,
                f"URL appears in multiple reading-list sections ({normalized}); first seen as {first_url!r} in ## {first_section}; all hits: {details}",
                first_line,
            )

    for target, hits in sorted(source_targets.items()):
        normalized_urls = {identity(url) for _, _, url in hits}
        if len(normalized_urls) <= 1:
            continue
        first_file, first_line, first_url = hits[0]
        details = "; ".join(f"{file_rel}:line {line}" for file_rel, line, _ in hits[:8])
        if len(hits) > 8:
            details += f"; ... {len(hits) - 8} more"
        add(
            findings,
            "warning",
            "source-note-multiple-reading-list-rows",
            first_file,
            (
                f"multiple completed reading-list URLs point to one source note [[{target}]]; "
                f"first URL {first_url!r}; rows: {details}. Independent queue URLs should "
                "normally create independent source notes."
            ),
            first_line,
        )


def _parse_iso_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _project_todo_section_key(title: str) -> str | None:
    """Map the template's historical heading variants to one semantic key."""
    normalized = title.strip()
    for key, pattern in PROJECT_TODO_SECTION_PATTERNS.items():
        if pattern.match(normalized):
            return key
    return None


def _check_project_todos(
    kb_root: Path,
    proj: Path,
    findings: list[dict[str, Any]],
    project_status: Any,
) -> None:
    """Advisory task-board format checks, preserving existing board heading variants."""
    todos = proj / "_todos.md"
    if not todos.exists():
        return
    try:
        text = todos.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        add(
            findings,
            "warning",
            "project-todos-unreadable",
            rel(todos, kb_root),
            f"could not inspect project task board: {exc}",
        )
        return

    sections: dict[str, list[int]] = {key: [] for key in PROJECT_TODO_SECTION_PATTERNS}
    current: str | None = None
    for line_number, line in project_status.visible_markdown_lines(text):
        heading = re.match(r"^ {0,3}##\s+(.+?)\s*$", line)
        if heading:
            current = _project_todo_section_key(heading.group(1))
            if current is not None:
                sections[current].append(line_number)
            continue
        if current is None:
            continue
        checkbox = PROJECT_TODO_CHECKBOX_RE.match(line)
        if checkbox and checkbox.group(1) not in PROJECT_TODO_ALLOWED_MARKERS:
            add(
                findings,
                "warning",
                "project-todos-invalid-checkbox",
                rel(todos, kb_root),
                f"unsupported checkbox marker {checkbox.group(1)!r} in {current} section",
                line_number,
            )

    for key, line_numbers in sections.items():
        if not line_numbers:
            add(
                findings,
                "warning",
                "project-todos-missing-section",
                rel(todos, kb_root),
                f"task board is missing the {key} section",
            )
        elif len(line_numbers) > 1:
            add(
                findings,
                "warning",
                "project-todos-duplicate-section",
                rel(todos, kb_root),
                f"task board has duplicate {key} sections at lines {line_numbers}",
                line_numbers[1],
            )


def _add_project_code_state_findings(
    kb_root: Path,
    summary_rel: str,
    record: dict[str, Any],
    comparison: dict[str, Any],
    current: dict[str, Any] | None,
    stale_days: int,
    findings: list[dict[str, Any]],
) -> None:
    status = record.get("status")
    if status == "partial":
        add(
            findings,
            "warning",
            "project-code-state-partial",
            summary_rel,
            "; ".join(record.get("warnings", [])) or "project code-state record is partial",
        )
    elif status in {"invalid", "future"}:
        code = "project-code-state-future" if status == "future" else "project-code-state-invalid"
        add(
            findings,
            "warning",
            code,
            summary_rel,
            "; ".join(record.get("warnings", [])) or f"project code-state record is {status}",
        )
    if status != "valid":
        return

    if comparison.get("age_advisory") == "stale":
        add(
            findings,
            "warning",
            "project-code-state-stale",
            summary_rel,
            f"code-state observation is {comparison.get('age_days')}d old (>{stale_days}d); "
            "refresh the metadata observation, not merely the summary modified date",
        )
    if current is None or current.get("status") != "observed" or not current.get("observation_complete"):
        detail = "; ".join(current.get("errors", [])) if current else "current repository observation unavailable"
        add(
            findings,
            "warning",
            "project-code-state-uncheckable",
            summary_rel,
            "persisted code-state record cannot be compared because the current repository observation is incomplete"
            + (f": {detail}" if detail else ""),
        )
    elif comparison.get("status") == "diverged":
        add(
            findings,
            "warning",
            "project-code-state-diverged",
            summary_rel,
            "persisted code-state metadata differs from the current local Git observation; "
            "dirty-state equality never proves unchanged working-tree content",
        )


def check_projects(kb_root: Path, findings: list[dict[str, Any]], stale_days: int, topic: str | None) -> None:
    # Project entries are not topic-scoped; skip this check when a single topic is requested.
    if topic:
        return
    projects_root = kb_root / "projects"
    if not projects_root.is_dir():
        return
    today = datetime.date.today()
    try:
        project_status = _load_project_status()
    except ImportError as exc:
        add(findings, "error", "project-status-tooling", "_tools/kb-project-status.py", str(exc))
        return
    for proj in sorted(p for p in projects_root.iterdir() if p.is_dir() and not p.is_symlink()):
        slug = proj.name
        if slug.startswith((".", "_")):
            continue
        for required in PROJECT_REQUIRED_FILES:
            if not (proj / required).exists():
                add(findings, "error", "project-missing-file", rel(proj, kb_root),
                    f"project '{slug}' is missing required {required}")
        summary = proj / "_summary.md"
        if not summary.exists():
            _check_project_todos(kb_root, proj, findings, project_status)
            continue
        try:
            summary_text = summary.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            add(findings, "warning", "project-summary-unreadable", rel(summary, kb_root), str(exc))
            _check_project_todos(kb_root, proj, findings, project_status)
            continue
        fm, fm_errors = project_status.parse_frontmatter(summary_text)
        summary_rel = rel(summary, kb_root)
        modified = _parse_iso_date(fm.get("modified"))
        is_active = fm.get("status") == "active"

        if fm.get("modified") and modified is None:
            add(
                findings,
                "warning",
                "project-summary-invalid-modified",
                summary_rel,
                "project summary modified date is not ISO-formatted; freshness checks are unknown",
            )

        # Edit-recency staleness: only nag for projects that claim to be active.
        if is_active and modified and (today - modified).days > stale_days:
            add(findings, "warning", "project-stale", summary_rel,
                f"active project '{slug}' _summary not updated in {(today - modified).days}d "
                f"(>{stale_days}d); run kb-project wrap")

        location = project_status.resolve_code_location(summary_text)
        if location.get("status") in {"ambiguous", "relative", "not-found", "non-directory", "error"}:
            add(
                findings,
                "warning",
                "project-code-location",
                summary_rel,
                str(location.get("error") or "code location is not usable"),
            )

        record = project_status.parse_code_state(
            fm,
            frontmatter_errors=fm_errors,
            now=datetime.datetime.now(datetime.timezone.utc),
        )
        current: dict[str, Any] | None = None
        comparison: dict[str, Any] = {"status": "unknown"}
        if location.get("status") == "ok":
            code_path = Path(location.get("resolved", location["path"]))
            git = project_status.git_info(code_path)
            if not git.get("is_repo"):
                git_errors = git.get("errors") or ["code location is not a Git repository"]
                add(
                    findings,
                    "warning",
                    "project-code-location",
                    summary_rel,
                    str(git_errors[0]),
                )
            last_commit = _parse_iso_date(git.get("last_commit_date"))
            if last_commit and modified:
                # Retain this historical finding for compatibility, but label
                # it as a document-date heuristic rather than code evidence.
                days = (last_commit - modified).days
                if days > stale_days:
                    add(
                        findings,
                        "warning",
                        "project-summary-behind-code",
                        summary_rel,
                        f"'{slug}' code last committed {last_commit} but _summary modified {modified} "
                        f"({days}d behind; document-date heuristic only, not implementation verification); "
                        "run kb-project wrap",
                    )
            current = project_status.current_code_observation(code_path, git=git)
            comparison = project_status.compare_code_state(
                record,
                current,
                stale_days=stale_days,
            )
        _add_project_code_state_findings(
            kb_root,
            summary_rel,
            record,
            comparison,
            current,
            stale_days,
            findings,
        )
        _check_project_todos(kb_root, proj, findings, project_status)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run structural lint checks for the KB.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--topic")
    parser.add_argument("--all", action="store_true", help="lint all topics and top-level KB files")
    parser.add_argument("--check", action="append", choices=["frontmatter", "authorship", "wikilinks", "media", "reading-lists", "projects", "all"], default=None)
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS, help="staleness window for the projects check")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    if not kb_root.exists():
        print(json.dumps({"error": f"KB root not found: {kb_root}"}))
        return 2
    if args.topic and args.all:
        print(json.dumps({"error": "use either --topic or --all, not both"}))
        return 2

    checks = set(args.check or ["all"])
    if "all" in checks:
        checks = {"frontmatter", "authorship", "wikilinks", "media", "reading-lists", "projects"}
    files = iter_md_files(kb_root, args.topic if not args.all else None)
    findings: list[dict[str, Any]] = []
    if "authorship" in checks:
        check_authorship(kb_root, files, findings)
    if "frontmatter" in checks:
        check_frontmatter(kb_root, files, findings)
    if "wikilinks" in checks:
        check_wikilinks(kb_root, files, findings)
    if "media" in checks:
        check_media(kb_root, files, findings)
    if "reading-lists" in checks:
        check_reading_lists(kb_root, args.topic if not args.all else None, findings)
    if "projects" in checks:
        check_projects(kb_root, findings, args.stale_days, args.topic if not args.all else None)

    result = {
        "kb_root": str(kb_root),
        "scope": args.topic or "all",
        "checks": sorted(checks),
        "finding_count": len(findings),
        "findings": findings,
    }
    print(json.dumps(result, indent=2))
    return 1 if any(f["severity"] in {"error", "warning"} for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
