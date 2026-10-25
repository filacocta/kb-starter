#!/usr/bin/env python3
"""Inventory one already-captured FxTwitter/X JSON payload.

This is a pure, stdin-only parser.  It does not fetch, follow, execute, or
write anything.  ``--status-id`` is mandatory: a payload must contain that
exact id in its explicit ``status`` (or, only when absent, ``tweet``) root
envelope.  There is no first-array-item fallback.

Usage::

    cat captured.json | python _tools/kb-fxtwitter-extract.py \
        --status-id 2068011987200786733 --strict-json

The result exposes top-level ``state``/``items``/``count`` channels named
``root``, ``sameauthor_continuations``, ``unknown_otherauthor``, ``quotes``,
``quote_media``, ``articles``, ``article_blocks``, ``article_entity_map``,
``article_media_entities``, ``article_covers``, ``direct_media``, and
``cards_and_exposed_links``.  Every item has an exact JSON payload path;
``text`` and ``raw_text`` are kept independently.  The exact input is retained
once as ``raw_payload``; item paths point back into that object instead of
repeating nested raw objects.  Quotes and uncertain thread entries never
become authored continuations.  Repeated status views and conflicting media
views remain separate; byte-identical media aliases retain all source paths.
Nested quotes are inventoried to a bounded depth and any depth/shape gaps are
listed in ``unhandled_payload_paths``.

Channel states are ``present``, ``empty``, ``missing``, ``unknown``, or
``malformed``.  A missing/unknown/malformed channel is therefore not silently
reported as ``none``.  ``omissions`` records only these structural states;
it is not a completeness assertion.  The parser makes no claim about full
thread completeness, truth, evidence quality, licensing, rights, or semantic
relevance.  URLs are inventoried and conservatively flagged only; they are
never opened.  The CLI bounds stdin bytes and decoded nesting depth, rejects
NaN/Infinity, and rejects duplicate object keys with ``--strict-json``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit


SCHEMA = "kb-fxtwitter-extract/v2"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DEPTH = 64
DEFAULT_MAX_QUOTE_DEPTH = 16
CHANNEL_NAMES = (
    "root", "sameauthor_continuations", "unknown_otherauthor", "quotes",
    "quote_media", "articles", "article_blocks", "article_entity_map",
    "article_media_entities", "article_covers", "direct_media",
    "cards_and_exposed_links",
)
ID_FIELDS = ("id", "tweet_id", "status_id")
AUTHOR_ID_FIELDS = ("id", "user_id", "author_id")
AUTHOR_NAME_FIELDS = ("screen_name", "username", "handle", "name")
MEDIA_CONTAINERS = ("photos", "videos", "animated_gifs", "gifs", "images", "media")
COVER_FIELDS = ("cover_media", "coverMedia", "cover", "cover_image", "coverImage")
URL_RE = re.compile(r"(?i)(?:https?|javascript|data|file|vbscript|ftp):[^\s<>\"'`]+")
MISSING = object()


class ExtractionError(ValueError):
    """Base class for fail-closed parser errors."""


class InputLimitError(ExtractionError):
    """Input exceeded a configured parser bound."""


class InvalidJSONError(ExtractionError):
    """Input is not acceptable standards JSON."""


class DuplicateKeyError(InvalidJSONError):
    """Strict JSON contains a duplicate object key."""


class RootIdentityError(ExtractionError):
    """The requested root cannot be established exactly."""


def path_key(path: str, key: Any) -> str:
    if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(str(key), ensure_ascii=False)}]"


def path_index(path: str, index: int) -> str:
    return f"{path}[{index}]"


def new_channel(state: str = "missing") -> dict[str, Any]:
    return {"state": state, "items": [], "count": 0}


def add(channel: dict[str, Any], item: Any, state: str = "present") -> None:
    channel["items"].append(item)
    channel["count"] = len(channel["items"])
    if state == "present":
        channel["state"] = "present"
    elif channel["state"] == "missing":
        channel["state"] = state


def set_state(channel: dict[str, Any], state: str) -> None:
    if channel["items"]:
        channel["state"] = "present"
        return
    rank = {"missing": 0, "empty": 1, "unknown": 2, "malformed": 3, "present": 4}
    if rank.get(state, 0) >= rank.get(channel["state"], 0):
        channel["state"] = state


def warn(warnings: list[dict[str, str]], code: str, path: str, message: str) -> None:
    warnings.append({"code": code, "payload_path": path, "message": message})


def add_unhandled(unhandled: list[dict[str, str]], path: str, reason: str) -> None:
    """Record a parser boundary without copying the skipped payload value."""
    entry = {"payload_path": path, "reason": reason}
    if entry not in unhandled:
        unhandled.append(entry)


def scalar_id(value: Any) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        value = str(value)
        return value or None
    return None


def _status_id_details(status: dict[str, Any]) -> dict[str, Any]:
    """Return every status-id alias, including invalid aliases.

    A first scalar alias is not enough to establish identity: an API payload
    containing ``id: [..]`` or a second disagreeing alias must remain visible
    as ambiguous rather than silently selecting whichever field happens to be
    first.
    """
    aliases = [(field, status[field], scalar_id(status[field])) for field in ID_FIELDS if field in status]
    scalar_aliases = [(field, value) for field, _raw, value in aliases if value is not None]
    invalid_aliases = [field for field, _raw, value in aliases if value is None]
    values = [value for _field, value in scalar_aliases]
    if not aliases:
        state = "missing"
    elif invalid_aliases or len(set(values)) > 1:
        state = "conflict"
    else:
        state = "known"
    return {
        "state": state,
        "id": scalar_aliases[0][1] if scalar_aliases else None,
        "id_field": scalar_aliases[0][0] if scalar_aliases else None,
        "aliases": values,
        "alias_fields": [field for field, _value in scalar_aliases],
        "invalid_aliases": invalid_aliases,
    }


def status_id(status: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    details = _status_id_details(status)
    return details["id"], details["id_field"], details["aliases"]


def known_status_id(status: dict[str, Any]) -> str | None:
    details = _status_id_details(status)
    return details["id"] if details["state"] == "known" else None


def _author_details(value: Any) -> dict[str, Any]:
    """Extract author aliases without treating a handle as a stable ID.

    Handles/display names are useful metadata but are not sufficient to
    classify a continuation as authored by the focal account.  Any invalid or
    disagreeing alias is retained as an ambiguity state for callers to surface.
    """
    if value is MISSING:
        return {
            "state": "missing", "id": None, "name": None,
            "id_aliases": [], "name_aliases": [], "name_alias_conflict": False, "invalid_id_aliases": [],
        }
    if isinstance(value, str):
        return {
            "state": "unknown" if value else "missing", "id": None,
            "name": value or None, "id_aliases": [],
            "name_aliases": [{"field": "value", "value": value}] if value else [], "name_alias_conflict": False,
            "invalid_id_aliases": [],
        }
    if value is None:
        return {
            "state": "missing", "id": None, "name": None,
            "id_aliases": [], "name_aliases": [], "name_alias_conflict": False, "invalid_id_aliases": [],
        }
    if not isinstance(value, dict):
        return {
            "state": "unknown", "id": None, "name": None,
            "id_aliases": [], "name_aliases": [], "name_alias_conflict": False, "invalid_id_aliases": [],
        }
    id_aliases = [(field, value[field], scalar_id(value[field])) for field in AUTHOR_ID_FIELDS if field in value]
    scalar_ids = [(field, ident) for field, _raw, ident in id_aliases if ident is not None]
    invalid_ids = [field for field, _raw, ident in id_aliases if ident is None]
    name_aliases = [
        (field, value[field])
        for field in AUTHOR_NAME_FIELDS
        if isinstance(value.get(field), str) and value[field]
    ]
    distinct_ids = {ident for _field, ident in scalar_ids}
    distinct_names = {name.casefold() for _field, name in name_aliases}
    # Display-name aliases are routinely different from handles.  Once a
    # scalar stable ID is present and its ID aliases agree, that ID is the
    # comparison authority; only missing/invalid/disagreeing IDs make the
    # author identity unknown.
    state = "known" if scalar_ids and not invalid_ids and len(distinct_ids) == 1 else "unknown"
    return {
        "state": state,
        "id": scalar_ids[0][1] if scalar_ids else None,
        "name": name_aliases[0][1] if name_aliases else None,
        "id_aliases": [{"field": field, "value": ident} for field, ident in scalar_ids],
        "name_aliases": [{"field": field, "value": name} for field, name in name_aliases],
        "name_alias_conflict": len(distinct_names) > 1,
        "invalid_id_aliases": invalid_ids,
    }


def author_parts(value: Any) -> tuple[str | None, str | None]:
    details = _author_details(value)
    return details["id"], details["name"]


def author_identity(status: dict[str, Any]) -> dict[str, Any]:
    value = status.get("author", MISSING)
    details = _author_details(value)
    return {
        "id": details["id"] if details["state"] == "known" else None, "name": details["name"],
        "present": value is not MISSING, "state": details["state"],
        "id_aliases": details["id_aliases"],
        "name_aliases": details["name_aliases"],
        "name_alias_conflict": details["name_alias_conflict"],
        "invalid_id_aliases": details["invalid_id_aliases"],
    }


def same_author(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_details = _author_details(left.get("author", MISSING))
    right_details = _author_details(right.get("author", MISSING))
    return (
        left_details["state"] == "known"
        and right_details["state"] == "known"
        and left_details["id"] == right_details["id"]
    )


def author_comparison(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool | None, str]:
    left_details = _author_details(left.get("author", MISSING))
    right_details = _author_details(right.get("author", MISSING))
    if left_details["state"] != "known" or right_details["state"] != "known":
        return None, "identity-unknown"
    return left_details["id"] == right_details["id"], "id-match" if left_details["id"] == right_details["id"] else "id-different"


def text_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "value", "content", "raw_text"):
            if isinstance(value.get(key), str):
                return value[key]
    return None


def text_differs(status: dict[str, Any]) -> bool:
    return (
        "text" in status and "raw_text" in status
        and text_value(status.get("text")) is not None
        and text_value(status.get("raw_text")) is not None
        and text_value(status.get("text")) != text_value(status.get("raw_text"))
    )


def text_view_state(status: dict[str, Any]) -> str:
    """Classify exposed text alternatives without treating malformed as equal."""
    if "text" not in status and "raw_text" not in status:
        return "missing"
    values = [text_value(status.get(key)) for key in ("text", "raw_text") if key in status]
    if any(value is None for value in values):
        return "unknown"
    return "conflict" if len(values) == 2 and values[0] != values[1] else "known"


def status_record(status: dict[str, Any], payload_path: str, channel: str, classification: str | None = None) -> dict[str, Any]:
    id_details = _status_id_details(status)
    identity_established = id_details["state"] == "known"
    ident = id_details["id"] if identity_established else None
    id_field = id_details["id_field"] if identity_established else None
    aliases = id_details["aliases"]
    text_present, raw_present = "text" in status, "raw_text" in status
    text, raw = status.get("text"), status.get("raw_text")
    return {
        "payload_path": payload_path, "channel": channel, "classification": classification,
        "id": ident, "id_field": id_field, "id_aliases": aliases,
        "id_alias_fields": id_details["alias_fields"], "id_invalid_aliases": id_details["invalid_aliases"],
        "id_state": id_details["state"],
        "author": status.get("author"), "author_identity": author_identity(status),
        "text": text, "text_present": text_present, "raw_text": raw, "raw_text_present": raw_present,
        "text_alternatives": {"text": text, "raw": raw},
        "text_raw_text_differ": text_differs(status), "text_view_state": text_view_state(status),
    }


def status_entry(value: Any, payload_path: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, dict):
        return None, payload_path
    if any(key in value for key in ID_FIELDS) or any(key in value for key in ("author", "text", "raw_text", "media", "quote", "article", "card")):
        return value, payload_path
    nested = value.get("status")
    if isinstance(nested, dict) and value.get("type") in (None, "status"):
        return nested, path_key(payload_path, "status")
    return None, payload_path


def validate_values(value: Any, path: str = "$", strict_keys: bool = False) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidJSONError(f"non-finite number at {path} is not valid JSON")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_values(item, path_index(path, index), strict_keys=strict_keys)
    elif isinstance(value, dict):
        for key, item in value.items():
            if strict_keys and not isinstance(key, str):
                raise InvalidJSONError(f"non-string object key at {path_key(path, key)}")
            validate_values(item, path_key(path, key), strict_keys=strict_keys)


def measure_depth(value: Any, limit: int, path: str = "$", depth: int = 0, seen: set[int] | None = None) -> None:
    if depth > limit:
        raise InputLimitError(f"JSON nesting exceeds --max-depth {limit} at {path}")
    if not isinstance(value, (dict, list)):
        return
    seen = set() if seen is None else seen
    marker = id(value)
    if marker in seen:
        raise InputLimitError(f"cyclic Python payload at {path}")
    seen.add(marker)
    iterator = enumerate(value) if isinstance(value, list) else value.items()
    for key, item in iterator:
        child_path = path_index(path, key) if isinstance(value, list) else path_key(path, key)
        measure_depth(item, limit, child_path, depth + 1, seen)
    seen.remove(marker)


def unsafe_url(url: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        reasons.append("control-character")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return True, ["malformed-url"]
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        reasons.append("non-http-scheme")
    elif not parsed.netloc:
        reasons.append("http-url-without-host")
    if parsed.username is not None or parsed.password is not None:
        reasons.append("credentials-in-url")
    return bool(reasons), reasons


def trim_url(value: str) -> str:
    while value and value[-1] in ".,;:!?":
        value = value[:-1]
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    while value.endswith("]") and value.count("]") > value.count("["):
        value = value[:-1]
    return value


def walk_urls(value: Any, path: str, seen: set[int] | None = None) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        for match in URL_RE.finditer(value):
            url = trim_url(match.group(0))
            if url:
                yield url, path
        return
    if not isinstance(value, (dict, list)):
        return
    seen = set() if seen is None else seen
    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_urls(item, path_index(path, index), seen)
    else:
        for key, item in value.items():
            yield from walk_urls(item, path_key(path, key), seen)


def add_urls(channel: dict[str, Any], value: Any, path: str, parent: dict[str, Any], parent_path: str, parent_channel: str, warnings: list[dict[str, str]]) -> None:
    for url, url_path in walk_urls(value, path):
        unsafe, reasons = unsafe_url(url)
        add(channel, {
            "kind": "exposed_url", "payload_path": url_path,
            "parent_status_path": parent_path, "parent_status_id": known_status_id(parent),
            "parent_channel": parent_channel, "url": url, "unsafe": unsafe, "unsafe_reasons": reasons,
        })
        if unsafe:
            warn(warnings, "unsafe_url_exposed", url_path, "URL is inventoried only and was not followed or executed")


def raw_media_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in (
        "url", "media_url_https", "media_url", "src", "image", "preview_image_url", "previewImageUrl",
        "original_img_url", "originalImgUrl",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
        if isinstance(candidate, dict):
            nested = raw_media_url(candidate)
            if nested:
                return nested
    for key in ("photo", "media", "cover", "image"):
        if isinstance(value.get(key), dict):
            nested = raw_media_url(value[key])
            if nested:
                return nested
    for key in ("media_info", "mediaInfo", "image_info", "imageInfo"):
        if isinstance(value.get(key), dict):
            nested = raw_media_url(value[key])
            if nested:
                return nested
    return None


def media_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return next((scalar_id(value.get(key)) for key in ("id", "media_id", "mediaId", "key") if scalar_id(value.get(key)) is not None), None)


def media_type(value: Any) -> str | None:
    if isinstance(value, dict):
        direct = next((value[key] for key in ("type", "kind", "media_type", "mediaType") if isinstance(value.get(key), str) and value[key]), None)
        if direct is not None:
            return direct
        for key in ("__typename", "typename"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        for key in ("media_info", "mediaInfo", "image_info", "imageInfo"):
            nested = value.get(key)
            if isinstance(nested, dict):
                nested_type = media_type(nested)
                if nested_type is not None:
                    return nested_type
        return None
    return "url" if isinstance(value, str) else None


def media_thumb(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return next((value[key] for key in ("thumbnail_url", "thumbnailUrl", "poster_url", "posterUrl", "poster", "preview_image_url") if isinstance(value.get(key), str) and value[key]), None)


def iter_values(value: Any, path: str) -> Iterator[tuple[Any, str]]:
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield item, path_index(path, index)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield item, path_key(path, key)
    else:
        yield value, path


def media_views(value: Any, path: str) -> tuple[list[tuple[Any, str, str]], list[tuple[str, str, str]]]:
    """Return media views and ``(code,path,message)`` structural warnings."""
    if isinstance(value, list):
        return [(item, path_index(path, index), "direct") for index, item in enumerate(value)], []
    if not isinstance(value, dict):
        return [], [("media_channel_malformed", path, "media is not an object or array")]
    containers: list[tuple[str, Any]] = []
    if "all" in value:
        containers.append(("all", value.get("all")))
        containers.extend((key, value.get(key)) for key in MEDIA_CONTAINERS if key in value)
    else:
        containers.extend((key, value.get(key)) for key in MEDIA_CONTAINERS if key in value)
        if not containers and (raw_media_url(value) is not None or media_id(value) is not None):
            containers.append(("direct", value))
    if not containers:
        return [], [("media_channel_unknown", path, "media object has no recognized item container")]
    views: list[tuple[Any, str, str]] = []
    warnings: list[tuple[str, str, str]] = []
    for key, container in containers:
        container_path = path_key(path, key)
        if isinstance(container, dict) and (raw_media_url(container) is not None or media_id(container) is not None):
            views.append((container, container_path, key))
        elif isinstance(container, (dict, list)):
            views.extend((item, item_path, key) for item, item_path in iter_values(container, container_path))
        elif container is None:
            warnings.append(("media_container_unknown", container_path, "media container is null/unknown"))
        else:
            warnings.append(("media_container_malformed", container_path, "media container is not an object or array"))
    return views, warnings


def media_record(value: Any, payload_path: str, container: str, parent: dict[str, Any], parent_path: str, parent_channel: str) -> dict[str, Any]:
    kind, url, thumb = media_type(value), raw_media_url(value), media_thumb(value)
    unsafe, reasons = unsafe_url(url) if url is not None else (False, [])
    is_video = isinstance(kind, str) and kind.casefold() in {"video", "gif", "animated_gif", "animated-gif"}
    return {
        "payload_path": payload_path, "payload_paths": [payload_path],
        "source_container": container, "source_containers": [container],
        "parent_status_path": parent_path, "parent_status_id": known_status_id(parent),
        "parent_channel": parent_channel, "id": media_id(value), "type": kind, "url": url,
        "unsafe": unsafe, "unsafe_reasons": reasons, "thumbnail_url": thumb,
        "poster_present": None if not is_video else thumb is not None,
    }


def media_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return item.get("id"), item.get("url"), item.get("type"), item.get("thumbnail_url")


def add_media(channel: dict[str, Any], views: Iterable[tuple[Any, str, str]], parent: dict[str, Any], parent_path: str, parent_channel: str, warnings: list[dict[str, str]]) -> list[dict[str, Any]]:
    existing = {(item.get("parent_status_path"), *media_key(item)): item for item in channel["items"] if isinstance(item, dict)}
    added: list[dict[str, Any]] = []
    for value, payload_path, container in views:
        if not isinstance(value, (dict, str)):
            item = {
                "payload_path": payload_path, "payload_paths": [payload_path], "source_container": container,
                "parent_status_path": parent_path, "parent_status_id": known_status_id(parent), "parent_channel": parent_channel,
                "id": None, "type": None, "url": None, "unsafe": False, "unsafe_reasons": [],
                "thumbnail_url": None, "poster_present": None, "valid": False,
            }
            add(channel, item)
            warn(warnings, "media_entry_malformed", payload_path, "media entry is not an object or URL string")
            added.append(item)
            continue
        item = media_record(value, payload_path, container, parent, parent_path, parent_channel)
        key = (parent_path, *media_key(item))
        if key in existing:
            prior = existing[key]
            prior["payload_paths"].append(payload_path)
            prior["source_containers"].append(container)
            prior.setdefault("alias_payload_paths", []).append(payload_path)
            continue
        existing[key] = item
        add(channel, item)
        added.append(item)
        if item["url"] is None:
            warn(warnings, "media_url_missing", payload_path, "media entry exposes no URL field")
        elif item["unsafe"]:
            warn(warnings, "unsafe_url_exposed", payload_path, "media URL is inventoried only and was not followed or executed")
        if item["poster_present"] is False:
            warn(warnings, "video_poster_missing", payload_path, "video/gif has no poster or thumbnail URL in this payload")
    return added


def emit_entity_map(entity_map: Any, entity_path: str, article_path: str, parent: dict[str, Any], parent_path: str, parent_channel: str, channel: dict[str, Any], warnings: list[dict[str, str]]) -> None:
    if not isinstance(entity_map, (dict, list)):
        set_state(channel, "malformed")
        warn(warnings, "article_entity_map_malformed", entity_path, "entityMap is not an object or array")
        return
    if not entity_map:
        set_state(channel, "empty")
        return
    entries = enumerate(entity_map) if isinstance(entity_map, list) else entity_map.items()
    for key, value in entries:
        value_path = path_index(entity_path, key) if isinstance(entity_map, list) else path_key(entity_path, key)
        add(channel, {
            "payload_path": value_path, "entity_key": None if isinstance(key, int) else key,
            "index": key if isinstance(key, int) else None, "article_path": article_path,
            "parent_status_path": parent_path, "parent_status_id": known_status_id(parent),
            "parent_channel": parent_channel, "valid": True,
        })


def _add_article_blocks(
    blocks: list[Any],
    blocks_path: str,
    variant: str,
    article_path: str,
    status: dict[str, Any],
    status_path: str,
    parent_channel: str,
    channel: dict[str, Any],
    warnings: list[dict[str, str]],
    alias_blocks_path: str | None = None,
) -> None:
    """Inventory one article block representation while retaining its path."""
    if not blocks:
        set_state(channel, "empty")
        return
    for index, block in enumerate(blocks):
        block_path = path_index(blocks_path, index)
        valid = isinstance(block, dict)
        payload_paths = [block_path]
        if alias_blocks_path is not None:
            payload_paths.append(path_index(alias_blocks_path, index))
        add(channel, {
            "payload_path": block_path, "payload_paths": payload_paths,
            "article_path": article_path, "parent_status_path": status_path,
            "parent_status_id": known_status_id(status), "parent_channel": parent_channel, "source_variant": variant,
            "index": index, "type": block.get("type") if valid else None,
            "text": block.get("text") if valid else None, "raw_text": block.get("raw_text") if valid else None,
            "valid": valid,
        })
        if not valid:
            warn(warnings, "article_block_malformed", block_path, "article block is not an object")


def extract_article(
    status: dict[str, Any],
    status_path: str,
    parent_channel: str,
    channels: dict[str, dict[str, Any]],
    warnings: list[dict[str, str]],
) -> None:
    if "article" not in status:
        return
    article_path, article = path_key(status_path, "article"), status.get("article")
    if not isinstance(article, dict):
        add(channels["articles"], {
            "payload_path": article_path, "parent_status_path": status_path,
            "parent_status_id": known_status_id(status), "parent_channel": parent_channel, "valid": False,
        }, state="malformed")
        warn(warnings, "article_channel_malformed", article_path, "article is not an object")
        return
    add(channels["articles"], {
        "payload_path": article_path, "parent_status_path": status_path, "parent_status_id": known_status_id(status),
        "parent_channel": parent_channel, "id": scalar_id(article.get("id") or article.get("article_id")),
        "title": article.get("title"), "description": article.get("description"),
        "created_at": article.get("created_at") or article.get("createdAt"),
        "modified_at": article.get("modified_at") or article.get("modifiedAt"),
    })

    content, content_path = article.get("content", MISSING), path_key(article_path, "content")
    top_blocks = article.get("blocks", MISSING)
    content_blocks: Any = MISSING
    content_shape = "missing"
    if content is MISSING:
        if not isinstance(top_blocks, list):
            warn(warnings, "article_content_missing", article_path, "article has no content object")
    elif not isinstance(content, dict):
        content_shape = "malformed"
        warn(warnings, "article_content_malformed", content_path, "article content is not an object")
    else:
        content_blocks = content.get("blocks", MISSING)
        if content_blocks is MISSING:
            if not isinstance(top_blocks, list):
                warn(warnings, "article_blocks_missing", content_path, "article content has no blocks array")
        elif not isinstance(content_blocks, list):
            content_shape = "malformed"
            warn(warnings, "article_blocks_malformed", path_key(content_path, "blocks"), "article blocks is not an array")

    top_shape = "missing"
    if top_blocks is not MISSING and not isinstance(top_blocks, list):
        top_shape = "malformed"
        warn(warnings, "article_blocks_malformed", path_key(article_path, "blocks"), "top-level article blocks is not an array")

    candidates: list[tuple[str, list[Any], str]] = []
    equal_blocks_alias_path: str | None = None
    if (
        content_blocks is not MISSING
        and top_blocks is not MISSING
        and not (isinstance(content_blocks, list) and isinstance(top_blocks, list) and content_blocks == top_blocks)
        and not (content_blocks == top_blocks)
    ):
        warn(
            warnings,
            "article_blocks_variant_conflict",
            article_path,
            "article content.blocks and top-level article.blocks differ; valid variants are inventoried and malformed ones remain explicit",
        )
    if isinstance(content_blocks, list) and isinstance(top_blocks, list):
        if content_blocks == top_blocks:
            candidates.append(("content", content_blocks, path_key(content_path, "blocks")))
            equal_blocks_alias_path = path_key(article_path, "blocks")
        else:
            candidates.extend([
                ("content", content_blocks, path_key(content_path, "blocks")),
                ("top-level", top_blocks, path_key(article_path, "blocks")),
            ])
    elif isinstance(content_blocks, list):
        candidates.append(("content", content_blocks, path_key(content_path, "blocks")))
    elif isinstance(top_blocks, list):
        candidates.append(("top-level-fallback", top_blocks, path_key(article_path, "blocks")))
        warn(
            warnings,
            "article_blocks_fallback",
            path_key(article_path, "blocks"),
            "using top-level article.blocks because content.blocks is unavailable or malformed",
        )

    if candidates:
        for variant, blocks, blocks_path in candidates:
            _add_article_blocks(
                blocks, blocks_path, variant, article_path, status, status_path,
                parent_channel, channels["article_blocks"], warnings,
                equal_blocks_alias_path if variant == "content" else None,
            )
    elif content_shape == "malformed" or top_shape == "malformed":
        set_state(channels["article_blocks"], "malformed")
    elif content is MISSING and top_blocks is MISSING:
        set_state(channels["article_blocks"], "missing")
    elif isinstance(content, dict) and content_blocks is MISSING and top_blocks is MISSING:
        set_state(channels["article_blocks"], "missing")
    else:
        set_state(channels["article_blocks"], "missing")

    if isinstance(content, dict):
        map_key = next((key for key in ("entityMap", "entity_map") if key in content), None)
        if map_key is not None:
            emit_entity_map(
                content[map_key], path_key(content_path, map_key), article_path, status,
                status_path, parent_channel, channels["article_entity_map"], warnings,
            )
    if not (isinstance(content, dict) and any(key in content for key in ("entityMap", "entity_map"))):
        map_key = next((key for key in ("entityMap", "entity_map") if key in article), None)
        if map_key is not None:
            emit_entity_map(
                article[map_key], path_key(article_path, map_key), article_path, status,
                status_path, parent_channel, channels["article_entity_map"], warnings,
            )

    media_entities, media_path = article.get("media_entities", MISSING), path_key(article_path, "media_entities")
    if media_entities is MISSING:
        set_state(channels["article_media_entities"], "missing")
    elif not isinstance(media_entities, (dict, list)):
        set_state(channels["article_media_entities"], "malformed")
        warn(warnings, "article_media_entities_malformed", media_path, "media_entities is not an object or array")
    else:
        if not media_entities:
            set_state(channels["article_media_entities"], "empty")
        values = (
            ((media_entities, media_path),)
            if isinstance(media_entities, dict) and (raw_media_url(media_entities) is not None or media_id(media_entities) is not None)
            else iter_values(media_entities, media_path)
        )
        for value, value_path in values:
            url = raw_media_url(value)
            unsafe, reasons = unsafe_url(url) if url is not None else (False, [])
            valid = isinstance(value, (dict, str))
            add(channels["article_media_entities"], {
                "payload_path": value_path, "article_path": article_path, "parent_status_path": status_path,
                "parent_status_id": known_status_id(status), "parent_channel": parent_channel, "id": media_id(value),
                "type": media_type(value), "url": url, "unsafe": unsafe, "unsafe_reasons": reasons,
                "valid": valid,
            })
            if not valid:
                warn(warnings, "article_media_entity_malformed", value_path, "article media entity is not an object or URL string")
            elif unsafe:
                warn(warnings, "unsafe_url_exposed", value_path, "article media URL is inventoried only and was not followed or executed")

    cover_key = next((key for key in COVER_FIELDS if key in article), None)
    if cover_key is None:
        set_state(channels["article_covers"], "missing")
    else:
        cover_path, cover = path_key(article_path, cover_key), article.get(cover_key)
        if cover is None:
            set_state(channels["article_covers"], "unknown")
            warn(warnings, "article_cover_unknown", cover_path, "cover field is null/unknown")
        else:
            url = raw_media_url(cover)
            unsafe, reasons = unsafe_url(url) if url is not None else (False, [])
            add(channels["article_covers"], {
                "payload_path": cover_path, "article_path": article_path, "parent_status_path": status_path,
                "parent_status_id": known_status_id(status), "parent_channel": parent_channel,
                "id": media_id(cover), "type": media_type(cover), "url": url,
                "unsafe": unsafe, "unsafe_reasons": reasons,
            })
            if unsafe:
                warn(warnings, "unsafe_url_exposed", cover_path, "article cover URL is inventoried only and was not followed or executed")


def add_cards_and_links(channel: dict[str, Any], status: dict[str, Any], status_path: str, parent_channel: str, warnings: list[dict[str, str]]) -> None:
    card = status.get("card", MISSING)
    if card is not MISSING:
        card_path = path_key(status_path, "card")
        if isinstance(card, (dict, list)):
            add(channel, {"kind": "card", "payload_path": card_path, "parent_status_path": status_path, "parent_status_id": known_status_id(status), "parent_channel": parent_channel})
            add_urls(channel, card, card_path, status, status_path, parent_channel, warnings)
        else:
            set_state(channel, "malformed")
            warn(warnings, "card_channel_malformed", card_path, "card is not an object or array")
    for key in ("text", "raw_text", "url", "entities", "urls", "links", "facets"):
        if key in status:
            add_urls(channel, status[key], path_key(status_path, key), status, status_path, parent_channel, warnings)
    article = status.get("article")
    if isinstance(article, dict):
        article_path = path_key(status_path, "article")
        content = article.get("content")
        content_keys: set[str] = set()
        if isinstance(content, dict):
            content_path = path_key(article_path, "content")
            for key in ("entityMap", "entity_map", "blocks"):
                if key in content:
                    content_keys.add(key)
                    add_urls(channel, content[key], path_key(content_path, key), status, status_path, parent_channel, warnings)
        for key in ("entityMap", "entity_map", "blocks"):
            if key in article and not (key in content_keys and isinstance(content, dict) and article[key] == content[key]):
                add_urls(channel, article[key], path_key(article_path, key), status, status_path, parent_channel, warnings)
        for key in ("url", "urls", "links", "facets"):
            if key in article:
                add_urls(channel, article[key], path_key(article_path, key), status, status_path, parent_channel, warnings)


def warn_text_views(status: dict[str, Any], status_path: str, warnings: list[dict[str, str]], subject: str = "status") -> None:
    state = text_view_state(status)
    if state == "conflict":
        warn(warnings, "text_raw_text_difference", status_path, f"{subject} text and raw_text expose different text representations")
    elif state == "unknown":
        warn(warnings, "text_view_unknown", status_path, f"{subject} text alternatives are missing or malformed")


def text_views_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_values = [text_value(left.get(key)) for key in ("text", "raw_text")]
    right_values = [text_value(right.get(key)) for key in ("text", "raw_text")]
    left_primary = next((value for value in left_values if value is not None), None)
    right_primary = next((value for value in right_values if value is not None), None)
    return left_primary is not None and right_primary is not None and left_primary != right_primary


def inventory_quote(
    quote: Any,
    parent: dict[str, Any],
    parent_path: str,
    parent_channel: str,
    channels: dict[str, dict[str, Any]],
    warnings: list[dict[str, str]],
    unhandled: list[dict[str, str]],
    depth: int,
    max_quote_depth: int,
) -> None:
    """Inventory a quote and its nested channels without following any URL."""
    quote_path = path_key(parent_path, "quote")
    parent_id = known_status_id(parent)
    if depth > max_quote_depth:
        add(channels["quotes"], {
            "payload_path": quote_path, "parent_status_path": parent_path, "parent_status_id": parent_id,
            "parent_channel": parent_channel, "id": None, "id_state": "unknown",
            "author_identity": {"state": "unknown"}, "depth": depth,
            "classification": "depth-limit", "valid": False,
        }, state="unknown")
        warn(warnings, "quote_depth_limit", quote_path, f"nested quote traversal stopped at --max-quote-depth {max_quote_depth}")
        add_unhandled(unhandled, quote_path, "nested quote exceeds the configured traversal depth")
        return
    if not isinstance(quote, dict):
        add(channels["quotes"], {
            "payload_path": quote_path, "parent_status_path": parent_path, "parent_status_id": parent_id,
            "parent_channel": parent_channel, "id": None, "id_state": "missing", "valid": False,
        }, state="unknown" if quote is None else "malformed")
        warn(warnings, "quote_channel_unknown" if quote is None else "quote_channel_malformed", quote_path, "quote is null/unknown or not an object")
        add_unhandled(unhandled, quote_path, "quote channel is not an object")
        return

    id_details = _status_id_details(quote)
    quote_id = id_details["id"] if id_details["state"] == "known" else None
    quote_record: dict[str, Any] = {
        "payload_path": quote_path, "parent_status_path": parent_path, "parent_status_id": parent_id,
        "parent_channel": parent_channel, "id": quote_id,
        "id_field": id_details["id_field"] if id_details["state"] == "known" else None,
        "id_aliases": id_details["aliases"], "id_alias_fields": id_details["alias_fields"],
        "id_invalid_aliases": id_details["invalid_aliases"], "id_state": id_details["state"],
        "author": quote.get("author"), "author_identity": author_identity(quote),
        "text": quote.get("text"), "raw_text": quote.get("raw_text"),
        "text_alternatives": {"text": quote.get("text"), "raw": quote.get("raw_text")},
        "text_raw_text_differ": text_differs(quote), "text_view_state": text_view_state(quote),
        "media": [], "media_count": 0, "depth": depth,
    }
    if "media" in quote:
        media_path = path_key(quote_path, "media")
        views, media_warnings = media_views(quote.get("media"), media_path)
        for code, path, message in media_warnings:
            warn(warnings, code, path, message)
        if not views:
            set_state(
                channels["quote_media"],
                "malformed" if any(code.endswith("malformed") for code, _, _ in media_warnings)
                else "unknown" if media_warnings else "empty",
            )
        quote_record["media"] = add_media(channels["quote_media"], views, quote, quote_path, "quote", warnings)
        quote_record["media_count"] = len(quote_record["media"])
    add(channels["quotes"], quote_record)
    warn_text_views(quote, quote_path, warnings, "quoted")

    # Quotes can carry the same article/entity/media/card shapes as a root or
    # continuation.  Keep their parent channel as ``quote`` so they cannot be
    # mistaken for authored thread material.
    extract_article(quote, quote_path, "quote", channels, warnings)
    add_cards_and_links(channels["cards_and_exposed_links"], quote, quote_path, "quote", warnings)
    nested = quote.get("quote", MISSING)
    if nested is not MISSING:
        inventory_quote(
            nested, quote, quote_path, "quote", channels, warnings, unhandled,
            depth + 1, max_quote_depth,
        )


def inventory_status_channels(
    status: dict[str, Any],
    status_path: str,
    parent_channel: str,
    channels: dict[str, dict[str, Any]],
    warnings: list[dict[str, str]],
    unhandled: list[dict[str, str]],
    max_quote_depth: int,
) -> None:
    quote = status.get("quote", MISSING)
    if quote is not MISSING:
        inventory_quote(
            quote, status, status_path, parent_channel, channels, warnings,
            unhandled, 1, max_quote_depth,
        )
    media = status.get("media", MISSING)
    if media is not MISSING:
        media_path = path_key(status_path, "media")
        views, media_warnings = media_views(media, media_path)
        for code, path, message in media_warnings:
            warn(warnings, code, path, message)
        if not views:
            set_state(
                channels["direct_media"],
                "malformed" if any(code.endswith("malformed") for code, _, _ in media_warnings)
                else "unknown" if media_warnings else "empty",
            )
        add_media(channels["direct_media"], views, status, status_path, parent_channel, warnings)
    extract_article(status, status_path, parent_channel, channels, warnings)
    add_cards_and_links(channels["cards_and_exposed_links"], status, status_path, parent_channel, warnings)


def parse_payload(
    payload: Any,
    status_id_value: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_quote_depth: int = DEFAULT_MAX_QUOTE_DEPTH,
    strict_json: bool = False,
) -> dict[str, Any]:
    """Parse one decoded payload; transport and duplicate-key checks are separate."""
    if not isinstance(status_id_value, str) or not status_id_value:
        raise RootIdentityError("status_id must be a non-empty string")
    if max_depth <= 0:
        raise InputLimitError("max_depth must be positive")
    if max_quote_depth <= 0:
        raise InputLimitError("max_quote_depth must be positive")
    measure_depth(payload, max_depth)
    validate_values(payload, strict_keys=strict_json)
    if not isinstance(payload, dict):
        raise RootIdentityError("top-level payload must be an object")
    root_key = "status" if "status" in payload else "tweet" if "tweet" in payload else None
    if root_key is None:
        raise RootIdentityError("payload has no explicit status/tweet root envelope")
    root, root_path = status_entry(payload.get(root_key), path_key("$", root_key))
    if root is None or not isinstance(root, dict):
        raise RootIdentityError(f"root envelope {path_key('$', root_key)} is missing or malformed")
    root_id_details = _status_id_details(root)
    if root_id_details["state"] == "missing":
        raise RootIdentityError(f"root status at {root_path} has no scalar id")
    if root_id_details["state"] != "known":
        raise RootIdentityError(f"root status at {root_path} has conflicting or malformed id aliases")
    root_value = root_id_details["id"]
    assert isinstance(root_value, str)
    if root_value != status_id_value:
        raise RootIdentityError(f"requested status id {status_id_value!r} does not match root id {root_value!r} at {root_path}")

    channels = {name: new_channel() for name in CHANNEL_NAMES}
    warnings: list[dict[str, str]] = []
    unhandled: list[dict[str, str]] = []
    selected_top_level = {root_key, "thread"}
    for key in payload:
        if key not in selected_top_level:
            top_path = path_key("$", key)
            add_unhandled(unhandled, top_path, "top-level field is not interpreted by this inventory")
    if root_key == "status" and "tweet" in payload:
        warn(warnings, "additional_root_envelope_present", "$.tweet", "tweet envelope was not selected because explicit status envelope is authoritative")
    if "tweets" in payload:
        warn(warnings, "thread_alias_unexamined", "$.tweets", "tweets array is not treated as a thread without an explicit thread channel")

    add(channels["root"], status_record(root, root_path, "root"))
    warn_text_views(root, root_path, warnings)
    root_author_details = _author_details(root.get("author", MISSING))
    if root_author_details["state"] == "missing":
        warn(warnings, "root_author_missing", root_path, "root author identity is missing")
    elif root_author_details["state"] != "known":
        warn(warnings, "root_author_identity_unknown", root_path, "root author identity is ambiguous or has no stable ID")

    examined: list[tuple[dict[str, Any], str, str]] = [(root, root_path, "root")]
    thread = payload.get("thread", MISSING)
    if thread is MISSING:
        set_state(channels["sameauthor_continuations"], "missing")
        set_state(channels["unknown_otherauthor"], "missing")
        warn(warnings, "thread_channel_missing", "$.thread", "payload has no thread array")
    elif thread is None:
        set_state(channels["sameauthor_continuations"], "unknown")
        set_state(channels["unknown_otherauthor"], "unknown")
        warn(warnings, "thread_channel_unknown", "$.thread", "thread channel is null/unknown")
    elif not isinstance(thread, list):
        set_state(channels["sameauthor_continuations"], "malformed")
        set_state(channels["unknown_otherauthor"], "malformed")
        warn(warnings, "thread_channel_malformed", "$.thread", "thread channel is not an array")
    elif not thread:
        set_state(channels["sameauthor_continuations"], "empty")
        set_state(channels["unknown_otherauthor"], "empty")
    else:
        for index, raw in enumerate(thread):
            item_path = path_index("$.thread", index)
            status, status_path = status_entry(raw, item_path)
            if status is None:
                classification = "null-entry" if raw is None else "malformed-entry"
                add(channels["unknown_otherauthor"], {
                    "payload_path": item_path, "channel": "unknown_otherauthor", "classification": classification,
                    "id": None, "author": raw.get("author") if isinstance(raw, dict) else None, "valid": False,
                }, state="unknown" if raw is None else "malformed")
                warn(warnings, "thread_entry_unknown", item_path, f"thread entry is {classification}")
                continue

            id_details = _status_id_details(status)
            current = id_details["id"] if id_details["state"] == "known" else None
            if id_details["state"] == "conflict":
                add(channels["unknown_otherauthor"], status_record(status, status_path, "unknown_otherauthor", "conflicting-id-aliases"), state="unknown")
                warn(warnings, "status_id_alias_conflict", status_path, "status has conflicting or malformed id aliases")
                warn_text_views(status, status_path, warnings)
                examined.append((status, status_path, "unknown_otherauthor"))
                continue
            if id_details["state"] == "missing":
                add(channels["unknown_otherauthor"], status_record(status, status_path, "unknown_otherauthor", "missing-id"), state="unknown")
                warn(warnings, "status_id_missing", status_path, "thread status has no scalar id; it cannot be an authored continuation")
                warn_text_views(status, status_path, warnings)
                examined.append((status, status_path, "unknown_otherauthor"))
                continue
            assert current is not None
            if current == root_value:
                add(channels["unknown_otherauthor"], status_record(status, status_path, "unknown_otherauthor", "root-duplicate-view"))
                warn(warnings, "duplicate_root_status_view", status_path, "thread repeats the explicit root id; retained outside authored continuations")
                if status != root:
                    if text_views_conflict(root, status):
                        warn(warnings, "duplicate_status_text_conflict", status_path, "duplicate root status views expose different text")
                    warn_text_views(status, status_path, warnings)
                    examined.append((status, status_path, "unknown_otherauthor"))
                continue

            comparison, comparison_reason = author_comparison(root, status)
            if comparison is True:
                destination, classification = "sameauthor_continuations", "same-author"
            else:
                destination = "unknown_otherauthor"
                if root_author_details["state"] != "known":
                    classification = "author-comparison-unknown"
                    warn(warnings, "continuation_author_comparison_unknown", status_path, "root author identity is unavailable or ambiguous")
                else:
                    current_author = _author_details(status.get("author", MISSING))
                    if current_author["state"] == "missing":
                        classification = "missing-author"
                        warn(warnings, "continuation_author_missing", status_path, "thread status has no author identity")
                    elif current_author["state"] != "known":
                        classification = "author-identity-ambiguous"
                        warn(warnings, "continuation_author_identity_unknown", status_path, "thread author identity is ambiguous or has no stable ID")
                    else:
                        classification = "other-author"
                if comparison_reason == "identity-unknown" and classification == "other-author":
                    classification = "author-comparison-unknown"
            add(channels[destination], status_record(status, status_path, destination, classification))
            warn_text_views(status, status_path, warnings)
            examined.append((status, status_path, destination))

    seen_ids = {root_value: root_path}
    seen_statuses: dict[str, tuple[dict[str, Any], str]] = {root_value: (root, root_path)}
    for status, status_path, _ in examined[1:]:
        current = known_status_id(status)
        if current is not None and current in seen_ids:
            if current != root_value:
                prior_status, _prior_path = seen_statuses[current]
                if text_views_conflict(prior_status, status):
                    warn(warnings, "duplicate_status_text_conflict", status_path, "duplicate status views expose different text")
                warn(warnings, "duplicate_status_id_view", status_path, f"status id {current!r} also appears at {seen_ids[current]}")
        elif current is not None:
            seen_ids[current] = status_path
        if current is not None:
            seen_statuses.setdefault(current, (status, status_path))
    if isinstance(thread, list) and thread:
        if not channels["sameauthor_continuations"]["items"] and channels["sameauthor_continuations"]["state"] == "missing":
            set_state(channels["sameauthor_continuations"], "empty")
        if not channels["unknown_otherauthor"]["items"] and channels["unknown_otherauthor"]["state"] == "missing":
            set_state(channels["unknown_otherauthor"], "empty")

    for status, status_path, parent_channel in examined:
        inventory_status_channels(
            status, status_path, parent_channel, channels, warnings, unhandled,
            max_quote_depth,
        )
    counts = {name: channels[name]["count"] for name in CHANNEL_NAMES}
    counts.update({
        "valid_statuses_examined": len(examined), "structural_warnings": len(warnings),
        "unhandled_payload_paths": len(unhandled),
    })
    return {
        "schema": SCHEMA, "requested_status_id": status_id_value, "root_payload_path": root_path,
        "raw_payload": payload, "unhandled_payload_paths": unhandled,
        **channels, "counts": counts,
        "omissions": [{"channel": name, "state": channels[name]["state"], "count": channels[name]["count"]} for name in CHANNEL_NAMES if channels[name]["state"] != "present"],
        "structural_warnings": warnings,
    }


def reject_constant(value: str) -> Any:
    raise InvalidJSONError(f"non-standard JSON constant {value!r} is not accepted")


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json_bytes(data: bytes, *, strict_json: bool = False, max_bytes: int = DEFAULT_MAX_BYTES) -> Any:
    if max_bytes <= 0:
        raise InputLimitError("max_bytes must be positive")
    if len(data) > max_bytes:
        raise InputLimitError(f"JSON input is {len(data)} bytes; limit is {max_bytes}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidJSONError(f"stdin is not UTF-8: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=strict_object if strict_json else dict, parse_constant=reject_constant)
    except ExtractionError:
        raise
    except json.JSONDecodeError as exc:
        raise InvalidJSONError(f"malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventory one supplied FxTwitter JSON payload without fetching or following links.")
    parser.add_argument("--status-id", required=True, help="Exact root status ID; no arbitrary first-item fallback.")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help=f"Maximum stdin bytes (default: {DEFAULT_MAX_BYTES}).")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help=f"Maximum decoded JSON depth (default: {DEFAULT_MAX_DEPTH}).")
    parser.add_argument("--max-quote-depth", type=int, default=DEFAULT_MAX_QUOTE_DEPTH, help=f"Maximum nested quote depth to inventory (default: {DEFAULT_MAX_QUOTE_DEPTH}).")
    parser.add_argument("--strict-json", action="store_true", help="Reject duplicate object keys; NaN/Infinity always fail.")
    return parser


def stdin_bytes(limit: int) -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.read(limit + 1)
    return value.encode("utf-8") if isinstance(value, str) else value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.max_bytes <= 0 or args.max_depth <= 0 or args.max_quote_depth <= 0:
            raise InputLimitError("--max-bytes, --max-depth, and --max-quote-depth must be positive")
        payload = load_json_bytes(stdin_bytes(args.max_bytes), strict_json=args.strict_json, max_bytes=args.max_bytes)
        result = parse_payload(payload, args.status_id, max_depth=args.max_depth, max_quote_depth=args.max_quote_depth, strict_json=args.strict_json)
    except ExtractionError as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
