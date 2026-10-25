#!/usr/bin/env python3
"""Fetch structured tweet data from fxtwitter for KB ingestion.

``--inventory status|thread`` is the source-facing path: it retrieves one
selected endpoint, passes the complete response through the strict offline
inventory helper, and emits only stdout JSON.  The legacy compact projection
remains available for compatibility but is convenience-only, not source
evidence.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FXTWITTER_STATUS_URL = "https://api.fxtwitter.com/2/status/{tweet_id}"
FXTWITTER_THREAD_URL = "https://api.fxtwitter.com/2/thread/{tweet_id}"

HTTP_RETRIES = 4
HTTP_BACKOFF_BASE = 1.5  # seconds; sleep = base * 2**attempt -> 1.5, 3, 6 (fxtwitter is intermittently flaky)
INVENTORY_SCHEMA = "kb-tweet-fetch/inventory/v1"
INVENTORY_ATTEMPTS = 1
_EXTRACTOR_MODULE: Any | None = None
INVENTORY_HOSTS = frozenset({
    "x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com",
    "api.fxtwitter.com", "www.x.com", "www.twitter.com", "www.fxtwitter.com",
    "www.vxtwitter.com", "www.fixupx.com",
})


def warn(message: str) -> None:
    print(f"kb-tweet-fetch: {message}", file=sys.stderr)


def dump(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch structured tweet data for KB ingestion.")
    parser.add_argument("url")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the legacy compact convenience JSON to stdout (not source evidence).",
    )
    parser.add_argument(
        "--inventory",
        choices=("status", "thread"),
        metavar="{status,thread}",
        help="Fetch exactly one endpoint and emit a strict raw-response inventory to stdout.",
    )
    parser.add_argument(
        "--out",
        help="Retired: output is stdout-only; no filesystem artifact is written.",
    )
    parser.add_argument("--max-thread", type=int, default=30, help="Maximum thread tweets to include.")
    parser.add_argument("--max-quote-chain", type=int, default=10, help="Maximum self-quote chain tweets to include.")
    return parser


@dataclass
class TweetFetch:
    tweet: dict[str, Any] | None
    error: str | None = None


class InventoryError(ValueError):
    """The bounded raw-response inventory could not be established."""


def _load_inventory_extractor() -> Any:
    """Load the shared pure FxTwitter payload inventory helper."""
    global _EXTRACTOR_MODULE
    if _EXTRACTOR_MODULE is not None:
        return _EXTRACTOR_MODULE
    # ``kb-fxtwitter-extract.py`` is hyphenated and is intentionally kept as a
    # separate stdin-only parser.  Import it without changing its CLI or state.
    extractor_path = Path(__file__).with_name("kb-fxtwitter-extract.py")
    spec = importlib.util.spec_from_file_location("kb_fxtwitter_extract_for_tweet_fetch", extractor_path)
    if spec is None or spec.loader is None:
        raise InventoryError(f"could not load payload inventory helper: {extractor_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _EXTRACTOR_MODULE = module
    return module


def parse_tweet_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    patterns = (
        r"(?:^|/)status(?:es)?/(\d+)(?:/|$)",
        r"(?:^|/)i/status/(\d+)(?:/|$)",
        r"^(\d+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            return match.group(1)

    query = urllib.parse.parse_qs(parsed.query)
    for key in ("tweet_id", "id"):
        values = query.get(key)
        if values and re.fullmatch(r"\d+", values[0]):
            return values[0]

    raise ValueError(f"could not parse tweet id from URL: {url}")


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        if value is not None:
            return str(value)
    getter = getattr(response, "getheader", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    return None


def _response_status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        getter = getattr(response, "getcode", None)
        value = getter() if callable(getter) else None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InventoryError("fxtwitter response did not expose an HTTP status") from exc


def _response_url(response: Any) -> str | None:
    getter = getattr(response, "geturl", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


class _InventoryNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_inventory_request(request: urllib.request.Request, timeout: int = 20):
    # A redirect would be another, unselected request. The default error
    # handler turns an unfollowed 3xx into HTTPError for the caller to report.
    return urllib.request.build_opener(_InventoryNoRedirect()).open(request, timeout=timeout)


def _inventory_response(endpoint_url: str, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    """Fetch one bounded response body without JSON decoding or retry/fallback."""
    request = urllib.request.Request(endpoint_url, headers={"User-Agent": "kb-tweet-fetch/1.0"})
    try:
        with _open_inventory_request(request, timeout=20) as response:
            status = _response_status(response)
            content_length_header = _response_header(response, "Content-Length")
            content_length: int | None = None
            if content_length_header is not None:
                try:
                    parsed_length = int(content_length_header)
                except ValueError:
                    parsed_length = None
                if parsed_length is not None and parsed_length >= 0:
                    content_length = parsed_length
                    if parsed_length > max_bytes:
                        raise InventoryError(
                            f"fxtwitter {endpoint_url} response exceeds the {max_bytes}-byte inventory limit"
                        )
            if status != 200:
                raise InventoryError(f"fxtwitter HTTP {status} for {endpoint_url}")
            # One sentinel byte distinguishes an exact-limit body from a
            # valid JSON prefix whose remaining response was not consumed.
            body = response.read(max_bytes + 1)
            if isinstance(body, str):
                body = body.encode("utf-8")
            if not isinstance(body, bytes):
                raise InventoryError("fxtwitter response body was not bytes")
            if len(body) > max_bytes:
                raise InventoryError(
                    f"fxtwitter response exceeds the {max_bytes}-byte inventory limit"
                )
            if content_length is not None and len(body) != content_length:
                raise InventoryError("fxtwitter response body does not match its declared Content-Length")
            provenance = {
                "request_url": endpoint_url,
                "response_url": _response_url(response),
                "http_status": status,
                "content_type": _response_header(response, "Content-Type"),
                "content_length": content_length,
                "attempts": INVENTORY_ATTEMPTS,
                "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return body, provenance
    except InventoryError:
        raise
    except urllib.error.HTTPError as exc:
        raise InventoryError(f"fxtwitter HTTP {exc.code} for {endpoint_url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InventoryError(f"fxtwitter request failed for {endpoint_url}: {exc}") from exc


def inventory_endpoint(tweet_id: str, endpoint: str, requested_url: str) -> dict[str, Any]:
    """Fetch and inventory exactly one FxTwitter endpoint, retaining no files."""
    if endpoint not in {"status", "thread"}:
        raise InventoryError("inventory endpoint must be exactly 'status' or 'thread'")
    locator = urllib.parse.urlsplit(requested_url)
    if (
        locator.scheme not in {"http", "https"}
        or locator.hostname not in INVENTORY_HOSTS
        or locator.username is not None
        or locator.password is not None
        or locator.port not in {None, 80 if locator.scheme == "http" else 443}
        or not re.fullmatch(r"[0-9]+", tweet_id)
        or parse_tweet_id(requested_url) != tweet_id
    ):
        raise InventoryError("inventory requires a documented public X/Twitter/FxTwitter locator with the exact status ID")
    extractor = _load_inventory_extractor()
    max_bytes = int(getattr(extractor, "DEFAULT_MAX_BYTES", 8 * 1024 * 1024))
    max_depth = int(getattr(extractor, "DEFAULT_MAX_DEPTH", 64))
    max_quote_depth = int(getattr(extractor, "DEFAULT_MAX_QUOTE_DEPTH", 16))
    endpoint_url = (
        FXTWITTER_STATUS_URL if endpoint == "status" else FXTWITTER_THREAD_URL
    ).format(tweet_id=tweet_id)
    body, transport = _inventory_response(endpoint_url, max_bytes)
    try:
        payload = extractor.load_json_bytes(body, strict_json=True, max_bytes=max_bytes)
        if isinstance(payload, dict) and "status" in payload and "tweet" in payload:
            raise InventoryError(
                "fxtwitter payload contains duplicate status/tweet root envelopes; refusing ambiguous inventory"
            )
        parsed = extractor.parse_payload(
            payload,
            tweet_id,
            max_depth=max_depth,
            max_quote_depth=max_quote_depth,
            strict_json=True,
        )
    except InventoryError:
        raise
    except Exception as exc:
        extractor_error = getattr(extractor, "ExtractionError", ())
        if extractor_error and isinstance(exc, extractor_error):
            raise
        raise InventoryError(f"fxtwitter payload inventory failed: {exc}") from exc

    digest = hashlib.sha256(body).hexdigest()
    result: dict[str, Any] = {
        "schema": INVENTORY_SCHEMA,
        "mode": "raw-response-inventory",
        "evidence_role": "raw-response-inventory",
        "url": requested_url,
        "requested_locator": requested_url,
        "canonical_origin_established": False,
        "endpoint": endpoint,
        "endpoint_url": endpoint_url,
        "requested_status_id": tweet_id,
        "extractor_schema": parsed.get("schema"),
        "limits": {
            "max_bytes": max_bytes,
            "max_depth": max_depth,
            "max_quote_depth": max_quote_depth,
            "attempts": INVENTORY_ATTEMPTS,
        },
        "transport": transport,
        "raw_response": {
            "sha256": digest,
            "byte_length": len(body),
            "encoding": "utf-8",
            "decoded_payload_key": "raw_payload",
            "raw_http_archive": False,
            "note": "raw_payload is the complete decoded JSON envelope; it is not a raw HTTP archive",
        },
        "raw_payload_note": "raw_payload is the complete decoded JSON envelope; it is not a raw HTTP archive",
    }
    result.update({key: value for key, value in parsed.items() if key != "schema"})
    return result


def inventory(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "out", None) is not None:
        raise InventoryError("--out is retired: the inventory path is stdout-only")
    if args.inventory not in {"status", "thread"}:
        raise InventoryError("inventory endpoint must be exactly 'status' or 'thread'")
    tweet_id = parse_tweet_id(args.url)
    return inventory_endpoint(tweet_id, args.inventory, args.url)


def _get_json_with_retry(url: str, label: str, valid) -> tuple[dict[str, Any] | None, str | None]:
    """GET JSON with exponential backoff. Retries on transient HTTP/network errors and on
    200-but-empty responses (fxtwitter intermittently returns no usable object). `valid(payload)`
    decides whether a parsed 200 body is acceptable."""
    request = urllib.request.Request(url, headers={"User-Agent": "kb-tweet-fetch/1.0"})
    last_error: str | None = None
    for attempt in range(HTTP_RETRIES):
        retryable = False
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = getattr(response, "status", response.getcode())
                body = response.read().decode("utf-8")
            if status != 200:
                last_error = f"fxtwitter HTTP {status} for {label}"
                retryable = status == 429 or 500 <= status < 600
            else:
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as exc:
                    last_error = f"fxtwitter malformed JSON for {label}: {exc}"
                    retryable = True
                else:
                    if valid(payload):
                        return payload, None
                    msg = payload.get("message") if isinstance(payload, dict) else None
                    last_error = f"fxtwitter returned no usable data for {label}" + (f": {msg}" if msg else "")
                    retryable = True  # 200-but-empty: the flaky case, worth retrying
        except urllib.error.HTTPError as exc:
            last_error = f"fxtwitter HTTP {exc.code} for {label}"
            retryable = exc.code == 429 or 500 <= exc.code < 600
        except urllib.error.URLError as exc:
            last_error = f"fxtwitter request failed for {label}: {exc.reason}"
            retryable = True
        except TimeoutError:
            last_error = f"fxtwitter request timed out for {label}"
            retryable = True
        if not retryable or attempt == HTTP_RETRIES - 1:
            break
        time.sleep(HTTP_BACKOFF_BASE * (2 ** attempt))
    return None, last_error


def _status_object(payload: Any) -> dict[str, Any] | None:
    # /2/status/ wraps the tweet under "status"; /i/status/ uses "tweet". Accept either.
    if not isinstance(payload, dict):
        return None
    for key in ("tweet", "status"):
        obj = payload.get(key)
        if isinstance(obj, dict) and obj:
            return obj
    return None


def fetch_tweet(tweet_id: str) -> TweetFetch:
    payload, error = _get_json_with_retry(
        FXTWITTER_STATUS_URL.format(tweet_id=tweet_id),
        f"tweet {tweet_id}",
        lambda p: _status_object(p) is not None,
    )
    if error or payload is None:
        return TweetFetch(None, error or f"fxtwitter returned nothing for tweet {tweet_id}")
    return TweetFetch(_status_object(payload))


def fetch_thread(tweet_id: str) -> tuple[list[dict[str, Any]], str | None]:
    payload, error = _get_json_with_retry(
        FXTWITTER_THREAD_URL.format(tweet_id=tweet_id),
        f"thread {tweet_id}",
        lambda p: isinstance(p, dict) and (isinstance(p.get("thread"), list) or isinstance(p.get("status"), dict)),
    )
    if error or payload is None:
        return [], error
    thread = payload.get("thread")
    if not isinstance(thread, list):
        return [], None
    tweets = [
        item
        for item in thread
        if isinstance(item, dict) and (item.get("type") or "status") == "status" and item.get("id")
    ]
    return tweets, None


def media_photos(tweet: dict[str, Any]) -> list[str]:
    media = tweet.get("media")
    if not isinstance(media, dict):
        return []
    photos = []
    for photo in media.get("photos") or []:
        if isinstance(photo, str):
            photos.append(photo)
        elif isinstance(photo, dict):
            url = photo.get("url")
            if isinstance(url, str):
                photos.append(url)
    return photos


def media_videos(tweet: dict[str, Any]) -> list[str]:
    media = tweet.get("media")
    if not isinstance(media, dict):
        return []
    # Videos may be under media.videos, or only in media.all with type video/gif.
    candidates: list[Any] = list(media.get("videos") or [])
    for item in media.get("all") or []:
        if isinstance(item, dict) and item.get("type") in ("video", "gif"):
            candidates.append(item)
    videos: list[str] = []
    for video in candidates:
        if isinstance(video, str):
            url = video
        elif isinstance(video, dict):
            url = video.get("url")
        else:
            url = None
        if isinstance(url, str) and url not in videos:
            videos.append(url)
    return videos


def _range_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _entity_map(article: dict[str, Any]) -> dict[str, Any]:
    content = article.get("content") if isinstance(article.get("content"), dict) else {}
    entity_map = content.get("entityMap") or content.get("entity_map") or article.get("entityMap") or article.get("entity_map")
    return entity_map if isinstance(entity_map, dict) else {}


def _entity_url(entity: Any) -> str | None:
    if not isinstance(entity, dict):
        return None
    data = entity.get("data") if isinstance(entity.get("data"), dict) else entity
    for key in ("url", "expanded_url", "expandedUrl", "href", "display_url"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _media_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if not isinstance(value, dict):
        return None
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    for key in ("url", "media_url_https", "media_url", "src", "image", "preview_image_url"):
        candidate = data.get(key)
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
        if isinstance(candidate, dict):
            nested = _media_url(candidate)
            if nested:
                return nested
    for key in ("photo", "media", "cover", "image"):
        nested = data.get(key)
        if isinstance(nested, dict):
            url = _media_url(nested)
            if url:
                return url
    return None


def _render_inline_text(text: str, block: dict[str, Any], entities: dict[str, Any]) -> str:
    if not text:
        return ""

    style_ranges = [item for item in block.get("inlineStyleRanges") or [] if isinstance(item, dict)]
    entity_ranges = [item for item in block.get("entityRanges") or [] if isinstance(item, dict)]
    boundaries = {0, len(text)}

    for item in [*style_ranges, *entity_ranges]:
        offset = max(0, min(len(text), _range_int(item.get("offset"))))
        length = max(0, _range_int(item.get("length")))
        boundaries.add(offset)
        boundaries.add(max(0, min(len(text), offset + length)))

    rendered: list[str] = []
    points = sorted(boundaries)
    for start, end in zip(points, points[1:]):
        segment = text[start:end]
        if not segment:
            continue

        active_styles = {
            str(item.get("style") or "").upper()
            for item in style_ranges
            if start >= _range_int(item.get("offset")) and end <= _range_int(item.get("offset")) + _range_int(item.get("length"))
        }
        if "CODE" in active_styles:
            segment = f"`{segment}`"
        if "BOLD" in active_styles:
            segment = f"**{segment}**"
        if "ITALIC" in active_styles:
            segment = f"*{segment}*"

        link_url = None
        for item in entity_ranges:
            offset = _range_int(item.get("offset"))
            length = _range_int(item.get("length"))
            if start >= offset and end <= offset + length:
                entity = entities.get(str(item.get("key")))
                link_url = _entity_url(entity)
                if link_url:
                    break
        if link_url:
            segment = f"[{segment}]({link_url})"
        rendered.append(segment)

    return "".join(rendered)


def article_to_markdown(article: dict[str, Any]) -> str | None:
    """Convert an fxtwitter X Article object into Markdown."""
    if not isinstance(article, dict):
        return None

    content = article.get("content") if isinstance(article.get("content"), dict) else {}
    blocks = content.get("blocks") or article.get("blocks") or []
    if not isinstance(blocks, list):
        return None

    entities = _entity_map(article)
    lines: list[str] = []
    title = article.get("title")
    if isinstance(title, str) and title.strip():
        lines.extend([f"# {title.strip()}", ""])

    usable_blocks = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or block.get("blockType") or "unstyled")
        text = block.get("text")
        text = text if isinstance(text, str) else ""
        rendered = _render_inline_text(text, block, entities).strip()

        if block_type in ("atomic", "media", "image"):
            url = _media_url(block) or _media_url(block.get("data"))
            if not url:
                for item in block.get("entityRanges") or []:
                    if isinstance(item, dict):
                        url = _media_url(entities.get(str(item.get("key"))))
                        if url:
                            break
            if url:
                lines.extend([f"![]({url})", ""])
                usable_blocks += 1
            continue

        if block_type in ("divider", "hr", "horizontal-rule", "horizontal_rule"):
            lines.extend(["---", ""])
            usable_blocks += 1
            continue

        if not rendered:
            continue

        prefix = {
            "header-one": "# ",
            "header-two": "## ",
            "header-three": "### ",
            "unordered-list-item": "- ",
            "ordered-list-item": "1. ",
            "blockquote": "> ",
        }.get(block_type, "")

        if block_type == "code-block":
            lines.extend(["```", text, "```", ""])
        else:
            lines.extend([f"{prefix}{rendered}", ""])
        usable_blocks += 1

    if usable_blocks == 0:
        return None
    return "\n".join(lines).strip()


def tweet_text(tweet: dict[str, Any]) -> str:
    # fxtwitter `raw_text` is a dict ({text, display_text_range, facets}); `text` is a plain string.
    raw = tweet.get("raw_text")
    if isinstance(raw, dict):
        raw = raw.get("text")
    if isinstance(raw, str) and raw.strip():
        return raw
    return tweet.get("text") or ""


def compact_tweet(tweet: dict[str, Any]) -> dict[str, Any]:
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    compact = {
        "id": str(tweet.get("id") or ""),
        "url": tweet.get("url"),
        "author": {
            "screen_name": author.get("screen_name"),
            "name": author.get("name"),
        },
        "text": tweet_text(tweet),
        "photos": media_photos(tweet),
        "videos": media_videos(tweet),
        "created": tweet.get("created_timestamp"),
    }
    article = tweet.get("article")
    if isinstance(article, dict):
        compact["article_title"] = article.get("title") if isinstance(article.get("title"), str) else None
        compact["article_markdown"] = article_to_markdown(article)
    return compact


def _follow_redirects(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "kb-tweet-fetch/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.geturl()


def resolve_article_status_id(url: str, follow_redirects=None) -> str | None:
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = f"https://{url}"
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not (host == "t.co" or host.endswith(".t.co") or host in ("x.com", "twitter.com") or host.endswith(".x.com")):
        return None
    direct_match = re.search(r"(?:^|/)i/article/(\d+)(?:/|$)", parsed.path)
    if direct_match and (host in ("x.com", "twitter.com") or host.endswith(".x.com")):
        return direct_match.group(1)

    resolver = follow_redirects or _follow_redirects
    try:
        final_url = resolver(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(final_url, str):
        return None

    final = urllib.parse.urlparse(final_url)
    final_host = final.netloc.lower()
    if final_host not in ("x.com", "twitter.com") and not final_host.endswith(".x.com"):
        return None
    match = re.search(r"(?:^|/)i/article/(\d+)(?:/|$)", final.path)
    return match.group(1) if match else None


def bare_link(text: str) -> str | None:
    match = re.fullmatch(r"\s*((?:https?://)?(?:t\.co|(?:www\.)?(?:x|twitter)\.com)/\S+)\s*", text or "")
    return match.group(1) if match else None


def merge_bare_article_link(compact: dict[str, Any] | None) -> None:
    if not compact:
        return
    url = bare_link(compact.get("text") or "")
    if not url:
        return
    article_id = resolve_article_status_id(url)
    if not article_id:
        return
    fetched = fetch_tweet(article_id)
    if fetched.error or not fetched.tweet:
        warn(fetched.error or f"fxtwitter returned nothing for article {article_id}")
        return
    article_compact = compact_tweet(fetched.tweet)
    compact["article_title"] = article_compact.get("article_title")
    compact["article_markdown"] = article_compact.get("article_markdown")


def author_screen_name(tweet: dict[str, Any]) -> str | None:
    author = tweet.get("author")
    if not isinstance(author, dict):
        return None
    value = author.get("screen_name")
    return str(value).lower() if value else None


def looks_like_thread_start(text: str) -> bool:
    return bool(re.search(r"\b1/\d*", text) or "🧵" in text or re.search(r"\bthread\b", text, re.IGNORECASE))


def collect_video_urls(
    main: dict[str, Any] | None,
    quote: dict[str, Any] | None,
    thread: list[dict[str, Any]],
    quote_chain: list[dict[str, Any]] | None = None,
) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for entry in [main, quote, *thread, *(quote_chain or [])]:
        if not entry:
            continue
        for url in entry.get("videos") or []:
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def collect_video_urls_by_context(
    main: dict[str, Any] | None,
    quote: dict[str, Any] | None,
    thread: list[dict[str, Any]],
    quote_chain: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    main_id = str(main.get("id") or "") if main else ""
    quote_id = str(quote.get("id") or "") if quote else ""
    quote_chain_ids = {str(item.get("id") or "") for item in quote_chain or []}
    return {
        "main": _dedupe_urls(list((main or {}).get("videos") or [])),
        "quote": [] if quote_id in quote_chain_ids else _dedupe_urls(list((quote or {}).get("videos") or [])),
        "same_author_thread": _dedupe_urls(
            [
                url
                for tweet in thread
                if str(tweet.get("id") or "") not in {main_id, quote_id} | quote_chain_ids
                for url in tweet.get("videos") or []
            ]
        ),
        "self_quote_chain": _dedupe_urls(
            [url for tweet in quote_chain or [] for url in tweet.get("videos") or []]
        ),
    }


def flatten_video_contexts(videos_by_context: dict[str, list[str]]) -> list[str]:
    return _dedupe_urls([url for urls in videos_by_context.values() for url in urls])


def build_thread(tweet_id: str, focal_author: str | None, max_thread: int) -> tuple[list[dict[str, Any]], list[str]]:
    raw_thread, error = fetch_thread(tweet_id)
    if error:
        warn(error)
        return [], [error]

    raw_chain = [tweet for tweet in raw_thread if author_screen_name(tweet) == focal_author]
    return [compact_tweet(tweet) for tweet in raw_chain[:max_thread]], []


def build_quote_chain(
    raw_main: dict[str, Any],
    focal_author: str | None,
    max_quote_chain: int,
) -> tuple[list[dict[str, Any]], bool]:
    pending_quote = raw_main.get("quote") if isinstance(raw_main.get("quote"), dict) else None
    if not pending_quote or author_screen_name(pending_quote) != focal_author:
        return [], False

    if not pending_quote.get("id"):
        return [], False

    quote_chain: list[dict[str, Any]] = []
    depth = 0
    while depth < max_quote_chain and pending_quote:
        cid = pending_quote.get("id")
        if not cid:
            break
        fetched = fetch_tweet(str(cid))
        if fetched.error or not fetched.tweet:
            warn(fetched.error or f"fxtwitter returned nothing for self-quote {cid}")
            break
        if author_screen_name(fetched.tweet) != focal_author:
            break

        quote_chain.append(compact_tweet(fetched.tweet))
        depth += 1

        next_quote = fetched.tweet.get("quote") if isinstance(fetched.tweet.get("quote"), dict) else None
        pending_quote = next_quote if next_quote and next_quote.get("id") else None

    truncated = bool(
        depth >= max_quote_chain
        and pending_quote
        and pending_quote.get("id")
        and author_screen_name(pending_quote) == focal_author
    )
    return quote_chain, truncated


def render_markdown(data: dict[str, Any]) -> str:
    main = data.get("main") or {}
    quote = data.get("quote")
    quote_chain = data.get("quote_chain") or []
    lines = [
        "# Tweet Fetch",
        "",
        f"- URL: {main.get('url') or data.get('url') or ''}",
        f"- Tweet ID: {main.get('id') or ''}",
        f"- Author: @{(main.get('author') or {}).get('screen_name') or ''} ({(main.get('author') or {}).get('name') or ''})",
        f"- Created: {main.get('created') or ''}",
        "",
        "## Main",
        "",
        main.get("text") or "",
        "",
    ]
    if len(quote_chain) > 1:
        lines.extend(["## Quote Chain", ""])
        for index, tweet in enumerate(quote_chain, start=1):
            lines.extend(
                [
                    f"### {index}. @{(tweet.get('author') or {}).get('screen_name') or ''}",
                    "",
                    tweet.get("text") or "",
                    "",
                    f"- URL: {tweet.get('url') or ''}",
                    "",
                ]
            )
    elif quote:
        lines.extend(
            [
                "## Quote",
                "",
                f"- URL: {quote.get('url') or ''}",
                f"- Author: @{(quote.get('author') or {}).get('screen_name') or ''} ({(quote.get('author') or {}).get('name') or ''})",
                "",
                quote.get("text") or "",
                "",
            ]
        )
    if data.get("thread"):
        lines.extend(["## Thread", ""])
        for index, tweet in enumerate(data["thread"], start=1):
            lines.extend(
                [
                    f"### {index}. @{(tweet.get('author') or {}).get('screen_name') or ''}",
                    "",
                    tweet.get("text") or "",
                    "",
                    f"- URL: {tweet.get('url') or ''}",
                    "",
                ]
            )
    if data.get("flags", {}).get("thread_error"):
        lines.extend(["## Note", "", "The thread could not be fully retrieved.", ""])
    if data.get("video_urls"):
        lines.extend(["## Video URLs", ""])
        lines.extend(f"- {url}" for url in data["video_urls"])
        lines.append("")
    videos_by_context = data.get("videos_by_context") or {}
    if any(videos_by_context.values()):
        lines.extend(["## Video URLs by Context", ""])
        for context, urls in videos_by_context.items():
            if not urls:
                continue
            lines.extend([f"### {context}", ""])
            lines.extend(f"- {url}" for url in urls)
            lines.append("")
    return "\n".join(lines)


def write_markdown_artifact(data: dict[str, Any], out: str | None) -> None:
    if out is None:
        return
    raise ValueError(
        "--out is retired: kb-tweet-fetch is stdout-only; use the protected evidence custody/publication workflow"
    )


def analyze(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if getattr(args, "out", None) is not None:
        raise ValueError(
            "--out is retired: kb-tweet-fetch is stdout-only; use the protected evidence custody/publication workflow"
        )
    if args.max_thread < 1:
        raise ValueError("--max-thread must be positive")
    if args.max_quote_chain < 0:
        raise ValueError("--max-quote-chain must be zero or positive")

    tweet_id = parse_tweet_id(args.url)
    fetched = fetch_tweet(tweet_id)
    warnings: list[str] = []
    if fetched.error:
        warn(fetched.error)
        warnings.append(fetched.error)
    if not fetched.tweet:
        result = {
            "url": args.url,
            "output_kind": "compact-convenience",
            "evidence_role": "convenience-only-not-source-evidence",
            "main": None,
            "quote": None,
            "quote_chain": [],
            "thread": [],
            "flags": {
                "looks_like_thread_start": False,
                "is_thread": False,
                "thread_error": True,
                "quote_chain_len": 0,
                "quote_chain_truncated": False,
            },
            "video_urls": [],
            "warnings": warnings,
        }
        write_markdown_artifact(result, args.out)
        return result, 1

    raw_main = fetched.tweet
    main = compact_tweet(raw_main)
    raw_quote = raw_main.get("quote") if isinstance(raw_main.get("quote"), dict) else None
    quote = compact_tweet(raw_quote) if raw_quote else None
    merge_bare_article_link(main)
    merge_bare_article_link(quote)
    focal_author = author_screen_name(raw_main)
    quote_chain, quote_chain_truncated = build_quote_chain(raw_main, focal_author, args.max_quote_chain)
    thread, thread_warnings = build_thread(tweet_id, focal_author, args.max_thread)
    warnings.extend(thread_warnings)
    if not thread:
        thread = [main]

    text = main["text"]
    is_thread_start = looks_like_thread_start(text)
    flags = {
        "looks_like_thread_start": is_thread_start,
        "is_thread": len(thread) > 1,
        "thread_error": bool(thread_warnings),
        "quote_chain_len": len(quote_chain),
        "quote_chain_truncated": quote_chain_truncated,
    }
    videos_by_context = collect_video_urls_by_context(main, quote, thread, quote_chain)
    result = {
        "url": args.url,
        "output_kind": "compact-convenience",
        "evidence_role": "convenience-only-not-source-evidence",
        "main": main,
        "quote": quote,
        "quote_chain": quote_chain,
        "thread": thread,
        "flags": flags,
        "video_urls": flatten_video_contexts(videos_by_context),
        "videos_by_context": videos_by_context,
        "warnings": warnings,
    }
    write_markdown_artifact(result, args.out)
    return result, 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out is not None:
        parser.error(
            "--out is retired: kb-tweet-fetch is stdout-only; use the protected evidence custody/publication workflow"
        )
    if args.inventory is not None:
        try:
            result = inventory(args)
        except Exception as exc:
            print(
                json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2
        dump(result)
        return 0
    try:
        result, code = analyze(args)
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        warn(f"total failure: {exc}")
        result = {
            "url": args.url,
            "output_kind": "compact-convenience",
            "evidence_role": "convenience-only-not-source-evidence",
            "main": None,
            "quote": None,
            "quote_chain": [],
            "thread": [],
            "flags": {
                "looks_like_thread_start": False,
                "is_thread": False,
                "thread_error": True,
                "quote_chain_len": 0,
                "quote_chain_truncated": False,
            },
            "video_urls": [],
            "warnings": [str(exc)],
        }
        code = 1
    if args.json or not args.out:
        dump(result)
    elif result.get("out"):
        warn(f"wrote {result.get('out')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
