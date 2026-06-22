from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kb_feed.errors import KbFeedError
from kb_feed.markdown import append_topic_reading_list, telegram_annotation
from kb_feed.security import Resolver
from kb_feed.state import validate_topic_slug
from kb_feed.urls import extract_urls_from_entities, normalize_url


LIVE_KB_ROOT = Path("~/Vault/knowledgebase").expanduser()


@dataclass(frozen=True)
class DryRunResult:
    topic_slug: str
    thread_id: int
    urls_written: tuple[str, ...]


@dataclass(frozen=True)
class JsonEntity:
    type: str
    offset: int = 0
    length: int = 0
    url: str | None = None


def dry_run_update(
    update_path: Path,
    kb_root: Path,
    topic_map: dict[int, str],
    *,
    resolver: Resolver | None = None,
) -> DryRunResult:
    kb_root = kb_root.resolve()
    if kb_root == LIVE_KB_ROOT:
        raise KbFeedError("Dry-run refuses to write to the live KB root.")

    message = _message_from_update(json.loads(update_path.read_text(encoding="utf-8")))
    thread_id = int(message.get("message_thread_id") or 0)
    if thread_id not in topic_map:
        raise KbFeedError(f"No topic mapping for thread_id={thread_id}.")

    topic_slug = validate_topic_slug(topic_map[thread_id])
    text = str(message.get("text") or message.get("caption") or "")
    entities = _entities_from_message(message)
    raw_urls = extract_urls_from_entities(text, entities)
    if not raw_urls:
        raise KbFeedError("No URL entities found in update.")

    today = date.today()
    thread_label = topic_slug if topic_slug != "__unfiltered__" else "unfiltered"
    annotation = telegram_annotation(thread_label, today)
    note = _message_note(text, entities)
    written: list[str] = []

    for raw_url in raw_urls:
        record = normalize_url(raw_url, resolver=resolver or socket.getaddrinfo)
        line = f"- [ ] {record.fetch_url} — {note} — {annotation}" if note else f"- [ ] {record.fetch_url} — {annotation}"
        if topic_slug == "__unfiltered__":
            raise KbFeedError("Dry-run unfiltered writes are not implemented yet.")
        reading_list = kb_root / "topics" / topic_slug / "reading-list.md"
        append_topic_reading_list(reading_list, line, today=today)
        written.append(record.fetch_url)

    return DryRunResult(topic_slug=topic_slug, thread_id=thread_id, urls_written=tuple(written))


def parse_topic_map(values: list[str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for value in values:
        thread_id_raw, separator, topic_slug = value.partition("=")
        if not separator or not thread_id_raw or not topic_slug:
            raise KbFeedError("Topic mappings must look like THREAD_ID=topic-slug.")
        out[int(thread_id_raw)] = validate_topic_slug(topic_slug)
    return out


def _message_from_update(update: dict) -> dict:
    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict):
        raise KbFeedError("Update does not contain a message or channel_post object.")
    return message


def _entities_from_message(message: dict) -> list[JsonEntity]:
    raw_entities = message.get("entities") or message.get("caption_entities") or []
    entities: list[JsonEntity] = []
    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        entities.append(
            JsonEntity(
                type=str(raw.get("type", "")),
                offset=int(raw.get("offset", 0)),
                length=int(raw.get("length", 0)),
                url=str(raw["url"]) if raw.get("url") else None,
            )
        )
    return entities


def _message_note(text: str, entities: list[JsonEntity]) -> str | None:
    spans = sorted(
        (int(entity.offset), int(entity.offset) + int(entity.length))
        for entity in entities
        if entity.type == "url"
    )
    if not spans:
        cleaned = " ".join(text.split())
        return cleaned or None
    chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            chunks.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        chunks.append(text[cursor:])
    cleaned = " ".join("".join(chunks).split())
    return cleaned or None
