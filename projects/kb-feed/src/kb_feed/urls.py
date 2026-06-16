from __future__ import annotations

import socket
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kb_feed.errors import UrlRejected
from kb_feed.security import Resolver, assert_public_hostname


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
TWITTER_EQUIV_HOSTS = {"x.com", "twitter.com", "fxtwitter.com"}


class MessageEntityLike(Protocol):
    type: str
    offset: int
    length: int
    url: str | None


@dataclass(frozen=True)
class UrlRecord:
    original_url: str
    canonical_url: str
    normalized_url: str
    fetch_url: str


def extract_urls_from_entities(
    text: str,
    entities: list[MessageEntityLike] | tuple[MessageEntityLike, ...],
    parse_entity: Callable[[MessageEntityLike], str] | None = None,
) -> list[str]:
    urls: list[str] = []
    for entity in entities:
        entity_type = str(getattr(entity, "type", ""))
        if entity_type == "text_link":
            url = str(getattr(entity, "url", "") or "").strip()
        elif entity_type == "url":
            url = _parse_entity_text(text, entity, parse_entity).strip()
        else:
            url = str(getattr(entity, "url", "") or "").strip()
        if url:
            urls.append(url)
    return urls


def normalize_url(
    raw_url: str,
    resolver: Resolver = socket.getaddrinfo,
    *,
    skip_dns_check: bool = False,
) -> UrlRecord:
    original = raw_url.strip()
    if not original:
        raise UrlRejected("URL is empty.")

    parsed = urlsplit(original)
    if parsed.scheme not in {"http", "https"}:
        raise UrlRejected("Only http and https URLs are accepted.")
    if not parsed.hostname:
        raise UrlRejected("URL must include a hostname.")

    hostname = _canonical_host(parsed.hostname)
    if not skip_dns_check:
        assert_public_hostname(parsed.hostname, resolver=resolver)

    path = _strip_trailing_slash(parsed.path)
    query = _normalized_query(parsed.query)
    canonical = urlunsplit((parsed.scheme.lower(), _netloc(hostname, parsed.port), path, query, ""))

    normalized_host = "x.com" if hostname in TWITTER_EQUIV_HOSTS else hostname
    normalized = urlunsplit(("https", _netloc(normalized_host, parsed.port), path, query, ""))

    fetch_host = "fxtwitter.com" if normalized_host == "x.com" else hostname
    fetch = urlunsplit((parsed.scheme.lower(), _netloc(fetch_host, parsed.port), path, query, ""))

    return UrlRecord(
        original_url=original,
        canonical_url=canonical,
        normalized_url=normalized,
        fetch_url=fetch,
    )


def _parse_entity_text(
    text: str,
    entity: MessageEntityLike,
    parse_entity: Callable[[MessageEntityLike], str] | None,
) -> str:
    if callable(parse_entity):
        return str(parse_entity(entity))
    return _slice_telegram_utf16(text, int(entity.offset), int(entity.length))


def _slice_telegram_utf16(text: str, offset: int, length: int) -> str:
    raw = text.encode("utf-16-le")
    start = offset * 2
    end = (offset + length) * 2
    return raw[start:end].decode("utf-16-le")


def _canonical_host(host: str) -> str:
    lowered = host.lower().rstrip(".")
    return lowered.removeprefix("www.")


def _netloc(host: str, port: int | None) -> str:
    return f"{host}:{port}" if port is not None else host


def _strip_trailing_slash(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped or "/"


def _normalized_query(query: str) -> str:
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
        and key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlencode(kept, doseq=True)
