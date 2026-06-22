from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kb_feed.state import StateStore, validate_topic_slug
from kb_feed.urls import normalize_url


QUEUE_URL_RE = re.compile(r"^-\s+\[[ xX]\]\s+(?P<url>https?://\S+)")


@dataclass(frozen=True)
class BootstrapResult:
    scanned: int
    inserted: int
    duplicates: int
    skipped: tuple[str, ...] = ()


def bootstrap_seen_urls(store: StateStore, kb_root: Path) -> BootstrapResult:
    kb_root = kb_root.resolve()
    scanned = 0
    inserted = 0
    duplicates = 0
    skipped: list[str] = []

    for topic_slug, url in _iter_existing_queue_urls(kb_root):
        scanned += 1
        try:
            topic_slug = validate_topic_slug(topic_slug)
            record = normalize_url(url, skip_dns_check=True)
        except Exception as exc:
            skipped.append(f"{topic_slug}: {url} ({exc})")
            continue

        if store.mark_seen_url(
            record.normalized_url,
            record.fetch_url,
            topic_slug=topic_slug,
        ):
            inserted += 1
        else:
            duplicates += 1

    return BootstrapResult(
        scanned=scanned,
        inserted=inserted,
        duplicates=duplicates,
        skipped=tuple(skipped),
    )


def _iter_existing_queue_urls(kb_root: Path):
    topics_dir = kb_root / "topics"
    if topics_dir.exists():
        for reading_list in sorted(topics_dir.glob("*/reading-list.md")):
            topic_slug = reading_list.parent.name
            yield from _iter_urls_from_file(reading_list, topic_slug)

    dive_queue = kb_root / "_dive-queue.md"
    if dive_queue.exists():
        current_topic: str | None = None
        for line in dive_queue.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current_topic = line.removeprefix("## ").strip()
                continue
            if current_topic is None:
                continue
            match = QUEUE_URL_RE.match(line)
            if match:
                yield current_topic, match.group("url")


def _iter_urls_from_file(path: Path, topic_slug: str):
    for line in path.read_text(encoding="utf-8").splitlines():
        match = QUEUE_URL_RE.match(line)
        if match:
            yield topic_slug, match.group("url")
