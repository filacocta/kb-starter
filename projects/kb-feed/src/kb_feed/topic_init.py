"""Create a new KB topic skeleton from a Telegram `map <slug>` command.

When Brendan maps a thread to a topic the KB does not have yet, kb-feed creates the
topic directory with kb-bot-authored skeleton files (matching KB conventions: the
canonical reading-list section ladder, a summary, and a research-ideas file) so the
topic behaves normally from then on. Best-effort `_index.md` row insertion keeps the
topic discoverable by the skills that read the index first.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

SUBDIRS = ("sources", "notes/concepts", "notes/entities", "notes/media", "notes/syntheses")

READING_LIST_LADDER = """## Pending

## Approved

## Auto-approved

## Processed

## Failed — Retry
"""


def _summary(slug: str, today: str) -> str:
    return (
        "---\n"
        "author: kb-bot\n"
        f"created: {today}\n"
        f"modified: {today}\n"
        "type: summary\n"
        f"topic: {slug}\n"
        f'title: "{slug}"\n'
        'description: "Auto-created topic from a Telegram `map` command; populate as sources arrive."\n'
        f'tags: ["{slug}"]\n'
        "---\n\n"
        f"# {slug}\n\n"
        f"*Auto-created by kb-feed on {today} from a Telegram `map {slug}` command. "
        "Populate as sources are ingested.*\n\n"
        "## Overview\n\n*(none yet)*\n\n"
        "## Key Concepts\n\n*(none yet)*\n\n"
        "## Sources\n\n*(none yet)*\n\n"
        "## Gaps & Open Questions\n\n"
        "- Topic created from a Telegram map command; no sources ingested yet.\n"
    )


def _reading_list(slug: str, today: str) -> str:
    return (
        "---\n"
        "author: kb-bot\n"
        f"created: {today}\n"
        f"modified: {today}\n"
        "type: meta\n"
        f"topic: {slug}\n"
        "---\n\n"
        f"# Reading List — {slug}\n\n"
        "Canonical section ladder (enforced by `_tools/kb-lint.py`): "
        "Pending -> Approved -> Auto-approved -> Processed -> Failed — Retry. Sub-group with `###`.\n\n"
        + READING_LIST_LADDER
    )


def _research_ideas(slug: str, today: str) -> str:
    return (
        "---\n"
        "author: kb-bot\n"
        f"created: {today}\n"
        f"modified: {today}\n"
        "type: research-ideas\n"
        f"topic: {slug}\n"
        f'title: "{slug} - Research Ideas"\n'
        f'description: "Prose-only rabbit holes and research questions for the {slug} topic."\n'
        "---\n\n"
        f"# {slug} - Research Ideas\n\n"
        "Prose-only rabbit holes and research questions. Add exact URLs to `reading-list.md` instead.\n\n"
        "## Ideas\n"
    )


def create_topic_skeleton(kb_root: Path, slug: str, today: date | None = None) -> bool:
    """Create topics/<slug>/ with kb-bot skeleton files. Returns False if it already existed."""
    topic_dir = kb_root / "topics" / slug
    if topic_dir.exists():
        return False
    iso = (today or date.today()).isoformat()
    for sub in SUBDIRS:
        (topic_dir / sub).mkdir(parents=True, exist_ok=True)
    (topic_dir / "_summary.md").write_text(_summary(slug, iso), encoding="utf-8")
    (topic_dir / "reading-list.md").write_text(_reading_list(slug, iso), encoding="utf-8")
    (topic_dir / "research-ideas.md").write_text(_research_ideas(slug, iso), encoding="utf-8")
    _try_add_index_row(kb_root, slug, iso)
    return True


_TOPIC_ROW_RE = re.compile(r"^\| \[\[knowledgebase/topics/")
_TOPICS_STAT_RE = re.compile(r"^- Topics:\s*(\d+)\s*$", re.MULTILINE)


def _try_add_index_row(kb_root: Path, slug: str, iso: str) -> None:
    """Best-effort: append a row to the _index.md topics table and bump the Topics count."""
    index = kb_root / "_index.md"
    if not index.exists():
        return
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return
    if f"topics/{slug}|" in text or f"topics/{slug}/_summary" in text:
        return  # already listed
    lines = text.splitlines()
    last_row = None
    for i, line in enumerate(lines):
        if _TOPIC_ROW_RE.match(line):
            last_row = i
    if last_row is None:
        return  # table not found; leave index untouched (a skill/lint will reconcile)
    row = (
        f"| [[knowledgebase/topics/{slug}|{slug}]] | "
        f"[[knowledgebase/topics/{slug}/_summary]] | 0 | 0 | {iso} |"
    )
    lines.insert(last_row + 1, row)
    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    # Bump the "- Topics: N" stat if present.
    match = _TOPICS_STAT_RE.search(new_text)
    if match:
        new_text = new_text[: match.start()] + f"- Topics: {int(match.group(1)) + 1}" + new_text[match.end():]
    index.write_text(new_text, encoding="utf-8")
