from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kb_feed.errors import KbFeedError, KbWriteRejected
from kb_feed.locking import exclusive_kb_write_lock
from kb_feed.markdown import (
    append_research_idea,
    append_topic_reading_list,
    assert_kb_bot_authored,
    update_modified_date,
)
from kb_feed.state import validate_topic_slug
from kb_feed.triage import LinkItem, parse_unchecked_inspirations, parse_unchecked_links


@dataclass(frozen=True)
class ReviewResult:
    source: str
    topic_slug: str
    target_line: str
    marked_source: bool
    added_reading_list: bool = False
    added_research_idea: bool = False


def assign_link(
    kb_root: Path,
    *,
    url: str,
    topic_slug: str,
    reason: str | None = None,
    today: date | None = None,
    lock_path: Path | None = None,
) -> ReviewResult:
    captured_on = today or date.today()
    topic_slug = validate_topic_slug(topic_slug)
    inbox_path = kb_root / "readinglist" / "Links.md"
    reading_list = kb_root / "topics" / topic_slug / "reading-list.md"

    with exclusive_kb_write_lock(kb_root, lock_path):
        item = _find_link(inbox_path, url)
        queue_reason = _single_line(reason) or _rest_without_separator(item.rest) or "assigned from global URL inbox"
        target_line = f"- [ ] {item.url} — {queue_reason}"

        added_reading = append_topic_reading_list(reading_list, target_line, today=captured_on)
        marked = _mark_checked(
            inbox_path,
            unchecked_prefix=f"- [ ] {item.display}",
            checked_line=f"- [x] {item.display} — assigned to {topic_slug} {captured_on.isoformat()}",
            today=captured_on,
        )
    return ReviewResult(
        source=item.display,
        topic_slug=topic_slug,
        target_line=target_line,
        marked_source=marked,
        added_reading_list=added_reading,
    )


def promote_inspiration(
    kb_root: Path,
    *,
    contains: str,
    topic_slug: str,
    idea: str | None = None,
    today: date | None = None,
    lock_path: Path | None = None,
) -> ReviewResult:
    captured_on = today or date.today()
    topic_slug = validate_topic_slug(topic_slug)
    source_path = kb_root / "readinglist" / "topics.md"
    research_ideas = kb_root / "topics" / topic_slug / "research-ideas.md"

    with exclusive_kb_write_lock(kb_root, lock_path):
        item_text = _find_inspiration(source_path, contains)
        target_text = _single_line(idea) or item_text
        target_line = f"- [ ] {target_text}"
        # Prose ideas go to the topic's research-ideas.md (## Ideas), not the generated _dive-queue.md.
        added_idea = append_research_idea(research_ideas, topic_slug, target_line, today=captured_on)
        marked = _mark_checked(
            source_path,
            unchecked_prefix=f"- [ ] {item_text}",
            checked_line=f"- [x] {item_text} — promoted to {topic_slug} research ideas {captured_on.isoformat()}",
            today=captured_on,
        )
    return ReviewResult(
        source=item_text,
        topic_slug=topic_slug,
        target_line=target_line,
        marked_source=marked,
        added_research_idea=added_idea,
    )


def _find_link(path: Path, url: str) -> LinkItem:
    matches = [item for item in parse_unchecked_links(path) if item.url == url or item.display == url]
    if not matches:
        raise KbFeedError(f"No unchecked link found in {path}: {url}")
    if len(matches) > 1:
        raise KbFeedError(f"Multiple unchecked links match {url}; edit the inbox or use the full displayed line.")
    return matches[0]


def _find_inspiration(path: Path, contains: str) -> str:
    needle = _single_line(contains).lower()
    if not needle:
        raise KbFeedError("--contains must not be empty.")
    matches = [item.text for item in parse_unchecked_inspirations(path) if needle in item.text.lower()]
    if not matches:
        raise KbFeedError(f"No unchecked inspiration note in {path} contains: {contains}")
    if len(matches) > 1:
        raise KbFeedError(f"Multiple unchecked inspiration notes contain {contains!r}; use a more specific substring.")
    return matches[0]


def _mark_checked(path: Path, *, unchecked_prefix: str, checked_line: str, today: date) -> bool:
    assert_kb_bot_authored(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line == unchecked_prefix:
            lines[index] = checked_line
            updated = update_modified_date("\n".join(lines) + "\n", today=today)
            _atomic_write_text(path, updated)
            return True
    raise KbWriteRejected(f"{path} no longer contains expected unchecked line: {unchecked_prefix}")


def _rest_without_separator(rest: str) -> str:
    return _single_line(re.sub(r"^\s+—\s+", "", rest))


def _single_line(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
