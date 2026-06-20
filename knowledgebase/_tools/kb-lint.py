#!/usr/bin/env python3
"""Structural lint checks for a knowledge base."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
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
READING_ITEM_STATUS_RE = re.compile(r"^\s*-\s+\[([ xX!~?])\]\s+")
LEVEL_2_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# Canonical reading-list section ladder (cross-agent contract; see _schema.md "Topic Reading Lists").
CANONICAL_READING_SECTIONS = ("Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry")


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


def check_authorship(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        if fm.get("author") != "kb-bot":
            add(findings, "error", "authorship", rel(path, kb_root), "missing author: kb-bot frontmatter")


def check_frontmatter(kb_root: Path, files: list[Path], findings: list[dict[str, Any]]) -> None:
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        file_rel = rel(path, kb_root)
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

    # Captured image media-description notes must carry an `## OCR` section so
    # that kb-ocr-enrich.py can fill it (the tool only fills existing sections,
    # never adds one). A captured image note without `## OCR` silently skips OCR.
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


def _section_for_line(lines: list[str], index: int) -> tuple[str, int]:
    section = "(before first heading)"
    section_line = 1
    for scan in range(index, -1, -1):
        match = LEVEL_2_HEADING_RE.match(lines[scan])
        if match:
            section = match.group(1).strip()
            section_line = scan + 1
            break
    return section, section_line


def iter_reading_lists(kb_root: Path, topic: str | None) -> list[Path]:
    if topic:
        return [kb_root / "topics" / topic / "reading-list.md"]
    return sorted(kb_root.glob("topics/*/reading-list.md"))


def check_reading_lists(kb_root: Path, topic: str | None, findings: list[dict[str, Any]]) -> None:
    kb_seen = _load_kb_seen(kb_root)
    seen_index = kb_seen.load_index(kb_root)

    for path in iter_reading_lists(kb_root, topic):
        if not path.exists():
            continue
        file_rel = rel(path, kb_root)
        lines = path.read_text(encoding="utf-8").splitlines()
        urls_by_norm: dict[str, list[tuple[str, int, str]]] = {}

        # Enforce the canonical section ladder (see _schema.md "Topic Reading Lists").
        # Only these five level-2 headings are permitted in a reading list; sub-grouping
        # uses level-3 (###) headings inside them. Non-ingestion prose goes to research-ideas.md.
        for index, line in enumerate(lines):
            heading = LEVEL_2_HEADING_RE.match(line)
            if not heading:
                continue
            name = heading.group(1).strip()
            if name not in CANONICAL_READING_SECTIONS:
                add(
                    findings,
                    "warning",
                    "reading-list-noncanonical-section",
                    file_rel,
                    f"Non-canonical reading-list section '## {name}'. Allowed level-2 headings: "
                    f"{', '.join(CANONICAL_READING_SECTIONS)}. Use ### sub-headings inside these, "
                    f"and move prose/research questions to research-ideas.md.",
                    index + 1,
                )

        for index, line in enumerate(lines):
            item_match = READING_ITEM_STATUS_RE.match(line)
            if not item_match:
                continue
            status = item_match.group(1).lower()
            section, _ = _section_for_line(lines, index)
            urls = URL_RE.findall(line)
            if not urls:
                continue
            primary_url = urls[0]
            normalized = kb_seen.normalize(primary_url)
            urls_by_norm.setdefault(normalized, []).append((primary_url, index + 1, section))
            if section.lower() == "auto-approved" and status != "x":
                source_note = kb_seen.is_ingested(primary_url, kb_root, seen_index)
                if source_note:
                    add(
                        findings,
                        "warning",
                        "auto-approved-already-ingested",
                        file_rel,
                        f"Auto-approved URL already ingested as {source_note}: {primary_url}",
                        index + 1,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run structural lint checks for the KB.")
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    parser.add_argument("--topic")
    parser.add_argument("--all", action="store_true", help="lint all topics and top-level KB files")
    parser.add_argument("--check", action="append", choices=["frontmatter", "authorship", "wikilinks", "media", "reading-lists", "all"], default=["all"])
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

    checks = set(args.check)
    if "all" in checks:
        checks = {"frontmatter", "authorship", "wikilinks", "media", "reading-lists"}
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
