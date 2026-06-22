#!/usr/bin/env python3
"""Derive the root `_dive-queue.md` dashboard from canonical topic queues.

Inputs:
- unchecked items under each topic reading-list's `## Auto-approved` section
- unchecked items in each topic's `research-ideas.md`

Default behavior is dry-run: print the would-be `_dive-queue.md` to stdout and
do not touch the live file. Use `--write` only after reviewing the output.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
from datetime import date
from pathlib import Path


LOCK_NAME = ".kb-write.lock"
LEVEL_2_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
CHECKBOX_RE = re.compile(r"^\s*-\s+\[ \]\s+")


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[: end + 5], text[end + 5 :]


def topic_slug(topic_dir: Path) -> str:
    return topic_dir.name


def title_for_topic(slug: str) -> str:
    return slug.replace("-", " ").title()


def unchecked_section_items(path: Path, section_name: str) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[str] = []
    in_section = False
    for line in lines:
        heading = LEVEL_2_HEADING_RE.match(line)
        if heading:
            in_section = heading.group(1).strip().lower() == section_name.lower()
            continue
        if in_section and CHECKBOX_RE.match(line):
            items.append(line.strip())
    return items


def collect_topic(topic_dir: Path) -> tuple[list[str], list[str]]:
    auto_items = unchecked_section_items(topic_dir / "reading-list.md", "Auto-approved")
    idea_items = unchecked_section_items(topic_dir / "research-ideas.md", "Ideas")
    return auto_items, idea_items


def render(kb_root: Path, today: date | None = None) -> str:
    stamp = (today or date.today()).isoformat()
    parts: list[str] = [
        "---",
        "author: kb-bot",
        f"created: {stamp}",
        f"modified: {stamp}",
        "type: meta",
        "---",
        "",
        "# Research Dive Queue",
        "",
        "> GENERATED FILE - DO NOT EDIT. Derived by `_tools/kb-derive-dive-queue.py` from topic `## Auto-approved` reading-list sections and `research-ideas.md` files.",
        "",
        "This dashboard is a read-only view. Add exact source URLs to topic `reading-list.md` files, and add prose-only rabbit holes to `topics/<topic>/research-ideas.md`.",
        "",
        "---",
        "",
    ]

    any_items = False
    for topic_dir in sorted((kb_root / "topics").iterdir()):
        if not topic_dir.is_dir():
            continue
        auto_items, idea_items = collect_topic(topic_dir)
        if not auto_items and not idea_items:
            continue
        any_items = True
        slug = topic_slug(topic_dir)
        parts.extend([f"## {slug}", ""])
        if auto_items:
            parts.extend(["### Auto-approved sources", ""])
            parts.extend(auto_items)
            parts.append("")
        if idea_items:
            parts.extend(["### Research ideas", ""])
            parts.extend(idea_items)
            parts.append("")

    if not any_items:
        parts.extend(["*(empty)*", ""])

    return "\n".join(parts).rstrip() + "\n"


def write_generated(kb_root: Path, output: str) -> None:
    target = kb_root / "_dive-queue.md"
    lock_path = kb_root / LOCK_NAME
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(output, encoding="utf-8")
            os.replace(tmp, target)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the derived `_dive-queue.md` dashboard.")
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    parser.add_argument("--dry-run", action="store_true", help="print output without writing; default behavior")
    parser.add_argument("--write", action="store_true", help="write `_dive-queue.md` after review")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    output = render(kb_root)
    if args.write:
        write_generated(kb_root, output)
        print(f"wrote {kb_root / '_dive-queue.md'}")
        return 0
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
