#!/usr/bin/env python3
"""Dry-run migration of prose-only `_dive-queue.md` items to research ideas.

This phase-8 helper intentionally defaults to dry-run. It reports unchecked
list items in the current root `_dive-queue.md` that contain no URL and shows
the `topics/<topic>/research-ideas.md` file each item would move to.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path


LOCK_NAME = ".kb-write.lock"
LEVEL_2_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
UNCHECKED_ITEM_RE = re.compile(r"^\s*-\s+\[ \]\s+")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def title_for_topic(slug: str) -> str:
    return slug.replace("-", " ").title()


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[: end + 5], text[end + 5 :]


def parse_prose_items(kb_root: Path) -> dict[str, list[str]]:
    path = kb_root / "_dive-queue.md"
    if not path.exists():
        raise FileNotFoundError(path)
    topic: str | None = None
    valid_topics = {p.name for p in (kb_root / "topics").iterdir() if p.is_dir()}
    items: dict[str, list[str]] = defaultdict(list)
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = LEVEL_2_HEADING_RE.match(line)
        if heading:
            candidate = heading.group(1).strip()
            topic = candidate if candidate in valid_topics else None
            continue
        if topic is None:
            continue
        if not UNCHECKED_ITEM_RE.match(line):
            continue
        if URL_RE.search(line):
            continue
        items[topic].append(line.strip())
    return dict(sorted(items.items()))


def research_ideas_template(topic: str, today: date) -> str:
    title = f"{title_for_topic(topic)} - Research Ideas"
    return "\n".join(
        [
            "---",
            "author: kb-bot",
            f"created: {today.isoformat()}",
            f"modified: {today.isoformat()}",
            "type: research-ideas",
            f"topic: {topic}",
            f'title: "{title}"',
            f'description: "Prose-only rabbit holes and research questions for the {topic} topic."',
            "---",
            "",
            f"# {title}",
            "",
            "Prose-only rabbit holes and research questions. Add exact URLs to `reading-list.md` instead.",
            "",
            "## Ideas",
            "",
        ]
    )


def update_research_ideas(kb_root: Path, items_by_topic: dict[str, list[str]], today: date | None = None) -> None:
    stamp = today or date.today()
    lock_path = kb_root / LOCK_NAME
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            for topic, items in items_by_topic.items():
                target = kb_root / "topics" / topic / "research-ideas.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    text = target.read_text(encoding="utf-8")
                    frontmatter, _ = split_frontmatter(text)
                    if "author: kb-bot" not in frontmatter:
                        raise RuntimeError(f"authorship guard failed: {target}")
                    lines = text.rstrip().splitlines()
                    if "## Ideas" not in lines:
                        lines.extend(["", "## Ideas"])
                else:
                    lines = research_ideas_template(topic, stamp).rstrip().splitlines()
                existing = set(lines)
                for item in items:
                    if item not in existing:
                        lines.append(item)
                output = "\n".join(lines).rstrip() + "\n"
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_text(output, encoding="utf-8")
                os.replace(tmp, target)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def render_report(kb_root: Path, items_by_topic: dict[str, list[str]]) -> str:
    lines: list[str] = []
    total = sum(len(items) for items in items_by_topic.values())
    lines.append(f"DRY RUN: would move {total} prose-only unchecked item(s) from `_dive-queue.md`.")
    lines.append("")
    if not items_by_topic:
        lines.append("No prose-only unchecked items found.")
        return "\n".join(lines) + "\n"
    for topic, items in items_by_topic.items():
        target = kb_root / "topics" / topic / "research-ideas.md"
        lines.append(target.relative_to(kb_root).as_posix())
        for item in items:
            lines.append(item)
        lines.append("")
    lines.append("No files modified.")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run migration of prose `_dive-queue.md` items to topic research ideas.")
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    parser.add_argument("--dry-run", action="store_true", help="report without writing; default behavior")
    parser.add_argument("--apply", action="store_true", help="append items to research-ideas.md files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    items_by_topic = parse_prose_items(kb_root)
    if args.apply:
        update_research_ideas(kb_root, items_by_topic)
        print(f"moved {sum(len(items) for items in items_by_topic.values())} item(s)")
        return 0
    print(render_report(kb_root, items_by_topic), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
