#!/usr/bin/env python3
"""Discover and capture media for the user's knowledge base."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import mimetypes
import os
import re
import struct
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - urllib fallback covers fresh systems.
    requests = None


USER_AGENT = "kb-media-capture/1.0 (+https://local/knowledgebase)"
LICENSES = {
    "public-domain",
    "cc-by",
    "cc-by-sa",
    "cc0",
    "quoted-with-attribution",
    "unknown",
    "do-not-redistribute",
}
CHROME_URL_DEFINITE = (
    "/static/images/",
    "/w/resources/",
    "Special:CentralAutoLogin",
    "wordmark",
    "favicon",
    "spacer.gif",
    "blank.gif",
    "/pixel.gif",
    "?type=1x1",
    "OOjs_UI_icon",
    "/themes/",
    "/usa-icons/",
    "us_flag",
    "_logo.",
    "/styles/list_item/",
    "/styles/masonry/",
    "/styles/thumbnail/",
)

CHROME_URL_PROBABLE = (
    "/icons/",
    "/copyright/",
    "/footer/",
    "tracking",
    "_compact.svg",
)


def is_chrome(item: dict[str, Any]) -> bool:
    """Drop UI chrome from discovered media.

    Definite-chrome URL patterns drop on URL alone (path patterns specific to
    CMS chrome directories, tracking pixels, branding marks).

    Probable-chrome patterns drop only when position_hint is also empty
    (the image lives outside any article heading).
    """
    url = item.get("origin_url") or ""
    if any(pat in url for pat in CHROME_URL_DEFINITE):
        return True
    if not item.get("position_hint"):
        if any(pat in url for pat in CHROME_URL_PROBABLE):
            return True
    return False


AUDIO_EXTS = {".mp3", ".wav", ".m4a"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}
EMBED_HOSTS = (
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
    "vimeo.com",
    "player.vimeo.com",
    "twitch.tv",
    "player.twitch.tv",
)

MIRROR_REWRITES = {
    "x.com": "fxtwitter.com",
    "www.x.com": "fxtwitter.com",
    "twitter.com": "fxtwitter.com",
    "www.twitter.com": "fxtwitter.com",
    "mobile.twitter.com": "fxtwitter.com",
}


def rewrite_for_mirror(url: str) -> tuple[str, str | None]:
    """Apply Rule 30 mirror rewriting. Returns (fetch_url, mirror_used_or_None).

    Canonical URL is preserved by callers; this only changes the host used for the HTTP fetch.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    new_host = MIRROR_REWRITES.get(host)
    if new_host is None:
        return url, None
    rewritten = urllib.parse.urlunsplit((parts.scheme or "https", new_host, parts.path, parts.query, parts.fragment))
    return rewritten, new_host


XCOM_STATUS_RE = re.compile(r"^/(?:i/web/)?(?:[A-Za-z0-9_]+/)?status/(\d+)")


def extract_xcom_status_id(url: str) -> str | None:
    """Return the numeric status ID if `url` is an x.com / twitter.com tweet URL, else None."""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    if host not in MIRROR_REWRITES:
        return None
    m = XCOM_STATUS_RE.match(parts.path)
    return m.group(1) if m else None


def discover_via_fxtwitter_api(source_url: str, status_id: str) -> tuple[dict[str, Any], int]:
    """Rule 30 fast-path for X.com posts. Uses api.fxtwitter.com/2/thread/<id> JSON.

    The HTML mirror (fxtwitter.com) User-Agent-sniffs and returns X's "JS not available" stub
    for non-browser clients, so we go straight to the JSON API which returns structured media.
    """
    api_url = f"https://api.fxtwitter.com/2/thread/{status_id}"
    result: dict[str, Any] = {
        "source_url": source_url,
        "mirror_used": "api.fxtwitter.com",
        "fetch_url": api_url,
        "fetched": False,
        "media": [],
        "errors": [],
    }
    try:
        text, _ = fetch_text(api_url)
        payload = json.loads(text)
    except Exception as exc:
        result["errors"].append(f"fxtwitter-api: {exc}")
        return result, 1
    status = payload.get("status") or (payload.get("tweets") or [{}])[0]
    if not isinstance(status, dict):
        result["errors"].append("fxtwitter-api: no status object in response")
        return result, 1
    media_block = status.get("media") or {}
    items = media_block.get("all") or (media_block.get("photos") or []) + (media_block.get("videos") or [])
    for m in items:
        if not isinstance(m, dict):
            continue
        mtype = (m.get("type") or "").lower()
        url = m.get("url")
        if not url:
            continue
        if mtype in ("photo", "image"):
            result["media"].append({
                "kind": "image",
                "origin_url": url,
                "alt": m.get("altText") or "",
                "caption": (status.get("text") or "")[:200],
                "position_hint": "x.com post body",
                "from_video": None,
            })
        elif mtype in ("video", "gif"):
            thumb = m.get("thumbnail_url") or m.get("thumbnailUrl")
            if thumb:
                result["media"].append({
                    "kind": "image",
                    "origin_url": thumb,
                    "alt": m.get("altText") or "",
                    "caption": (status.get("text") or "")[:200],
                    "position_hint": "x.com post body (video poster)",
                    "from_video": url,
                })
    result["fetched"] = True
    return result, 0 if result["media"] else 1


def dump(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        dump({"captured": [], "failed": [{"origin_url": None, "reason": message}]})
        raise SystemExit(1)


def fetch_bytes(url: str, max_bytes: int | None = None) -> tuple[bytes, str, str | None]:
    if urllib.parse.urlsplit(url).scheme == "file":
        path = Path(urllib.request.url2pathname(urllib.parse.urlsplit(url).path))
        chunks: list[bytes] = []
        total = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ValueError("bytes-cap")
                chunks.append(chunk)
        guessed, _ = mimetypes.guess_type(str(path))
        return b"".join(chunks), guessed or "", None

    if requests is not None:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, stream=True, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(str(response.status_code))
        # Preflight: reject before downloading if the server advertises a size
        # over the budget (saves streaming up to the cap for huge media).
        preflight = response.headers.get("content-length")
        if max_bytes is not None and preflight and preflight.isdigit() and int(preflight) > max_bytes:
            response.close()
            raise ValueError("bytes-cap")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("bytes-cap")
            chunks.append(chunk)
        return b"".join(chunks), response.headers.get("content-type", ""), response.headers.get("content-disposition")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - caller supplies URL by design.
        # Preflight: reject before downloading if advertised size exceeds budget.
        preflight = response.headers.get("content-length")
        if max_bytes is not None and preflight and preflight.isdigit() and int(preflight) > max_bytes:
            raise ValueError("bytes-cap")
        chunks = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("bytes-cap")
            chunks.append(chunk)
        return b"".join(chunks), response.headers.get("content-type", ""), response.headers.get("content-disposition")


def fetch_text(url: str) -> tuple[str, str]:
    body, content_type, _ = fetch_bytes(url, None)
    charset = "utf-8"
    match = re.search(r"charset=([^\s;]+)", content_type, re.I)
    if match:
        charset = match.group(1).strip("\"'")
    return body.decode(charset, errors="replace"), content_type


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def snippet(value: str, limit: int = 200) -> str:
    value = clean_space(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def slugify(value: str, fallback: str = "media") -> str:
    value = value.lower().encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or fallback


def cap_slug(value: str, limit: int) -> str:
    value = slugify(value)
    if len(value) <= limit:
        return value
    value = value[:limit].rstrip("-")
    return value or "media"


def url_without_fragment(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def filename_from_disposition(header: str | None) -> str:
    if not header:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", header, re.I)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', header, re.I)
    return match.group(1).strip() if match else ""


def basename_descriptor(url: str, disposition: str | None) -> str:
    name = filename_from_disposition(disposition)
    if not name:
        path = urllib.parse.urlsplit(url).path
        name = Path(urllib.parse.unquote(path)).name
    stem = Path(name).stem if name else ""
    return slugify(stem, "media")


def ext_from_url(url: str) -> str:
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if ext == ".jpeg":
        return ".jpg"
    return ext


def ext_from_mime(mime: str) -> str:
    mime = mime.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "video/mp4": ".mp4",
    }
    return mapping.get(mime) or mimetypes.guess_extension(mime) or ""


def canonical_mime(content_type: str, url: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime:
        return mime
    guessed, _ = mimetypes.guess_type(urllib.parse.urlsplit(url).path)
    return guessed or "application/octet-stream"


def kind_for(url: str, mime: str) -> str:
    ext = ext_from_url(url)
    if mime.startswith("image/") or ext in IMAGE_EXTS:
        return "image"
    if mime.startswith("audio/") or ext in AUDIO_EXTS:
        return "audio"
    if mime.startswith("video/") or ext in VIDEO_EXTS:
        return "video"
    return "image"


def dir_for_kind(kind: str) -> str:
    return {"image": "images", "video": "videos", "audio": "audio"}.get(kind, f"{kind}s")


def image_dimensions(data: bytes, mime: str) -> tuple[int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return webp_dimensions(data)
    if data.startswith(b"\xff\xd8"):
        return jpeg_dimensions(data)
    if mime == "image/svg+xml" or data.lstrip().startswith(b"<svg"):
        return svg_dimensions(data)
    return None, None


def jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        while marker == 0xFF and i < len(data):
            marker = data[i]
            i += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            break
        length = struct.unpack(">H", data[i : i + 2])[0]
        if length < 2 or i + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if i + 7 <= len(data):
                height = struct.unpack(">H", data[i + 3 : i + 5])[0]
                width = struct.unpack(">H", data[i + 5 : i + 7])[0]
                return width, height
        i += length
    return None, None


def webp_dimensions(data: bytes) -> tuple[int | None, int | None]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        return struct.unpack("<HH", data[26:30])
    if chunk == b"VP8L" and len(data) >= 25:
        b0, b1, b2, b3 = data[21:25]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return width, height
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    return None, None


def svg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    text = data[:4096].decode("utf-8", errors="ignore")
    match = re.search(r"<svg\b[^>]*\bwidth=['\"]?([0-9.]+)[a-z%]*['\"]?[^>]*\bheight=['\"]?([0-9.]+)", text, re.I)
    if match:
        return int(float(match.group(1))), int(float(match.group(2)))
    match = re.search(r"<svg\b[^>]*\bviewBox=['\"]\s*[-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)", text, re.I)
    if match:
        return int(float(match.group(1))), int(float(match.group(2)))
    return None, None


def _first_url_from_srcset(srcset: str) -> str:
    """Extract the first URL from an srcset / data-srcset attribute.

    srcset format: "url1 1x, url2 2x" or "url1 480w, url2 800w" etc.
    We return the first URL only — downstream dedup + chrome filter handles the rest.
    """
    if not srcset:
        return ""
    first_candidate = srcset.split(",", 1)[0].strip().split()
    return first_candidate[0] if first_candidate else ""


class MediaHTMLParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.media: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.heading: str = ""
        self.capture_heading: str | None = None
        self.in_heading: str | None = None
        self.in_figure = 0
        self.in_figcaption = 0
        self.caption_parts: list[str] = []
        self.pending_fig_media: list[int] = []
        self.in_p = 0
        self.p_parts: list[str] = []
        self.last_paragraph = ""
        self.video_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): v or "" for k, v in attrs_list}
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.in_heading = tag.upper()
            self.capture_heading = ""
        elif tag == "figure":
            self.in_figure += 1
            self.pending_fig_media = []
        elif tag == "p":
            self.in_p += 1
            self.p_parts = []
        elif tag == "figcaption":
            self.in_figcaption += 1
            self.caption_parts = []
        elif tag == "img":
            # Primary src first; fall back to lazy-load attrs commonly used to defer non-critical images.
            # Many news/CMS sites (NPR, Frontiers, Astro builds) set src= to a transparent placeholder
            # and the real URL to data-src / data-original / data-srcset / srcset. The defer is HTML-static
            # (no JS execution required at our end), the URL just lives in a different attribute.
            candidates = [
                attrs.get("src", ""),
                attrs.get("data-src", ""),
                attrs.get("data-original", ""),
                _first_url_from_srcset(attrs.get("data-srcset", "")),
                _first_url_from_srcset(attrs.get("srcset", "")),
            ]
            alt = attrs.get("alt", "")
            seen_local: set[str] = set()
            for cand in candidates:
                resolved = self.resolve(cand)
                if resolved and resolved not in seen_local:
                    seen_local.add(resolved)
                    self.add_media("image", resolved, alt, self.last_paragraph, self.position(), None)
        elif tag == "video":
            video_src = self.resolve(attrs.get("src", ""))
            poster = self.resolve(attrs.get("poster", ""))
            video_item: dict[str, Any] = {"src": video_src, "poster": poster, "poster_index": None}
            if poster:
                video_item["poster_index"] = self.add_media("image", poster, attrs.get("alt", "video poster"), "", f"inside <video> {self.position()}", video_src or None)
            self.video_stack.append(video_item)
        elif tag == "source" and self.video_stack:
            src = self.resolve(attrs.get("src", ""))
            current = self.video_stack[-1]
            if src and not current.get("src"):
                current["src"] = src
                poster_index = current.get("poster_index")
                if poster_index is not None and not self.media[poster_index].get("from_video"):
                    self.media[poster_index]["from_video"] = src
        elif tag == "iframe":
            origin = self.resolve(attrs.get("src", ""))
            if origin and is_video_embed(origin):
                label = attrs.get("title", "")
                self.add_media("video-embed", origin, "", snippet(f"Video: {label}") if label else "", self.position(), None)
        elif tag == "audio":
            origin = self.resolve(attrs.get("src", ""))
            if origin:
                self.add_media("audio", origin, "", "", self.position(), None)
        elif tag == "a":
            origin = self.resolve(attrs.get("href", ""))
            if origin and ext_from_url(origin) in AUDIO_EXTS:
                self.add_media("audio", origin, "", "", self.position(), None)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.in_heading and tag == self.in_heading.lower():
            self.heading = snippet(self.capture_heading or "", 80)
            self.in_heading = None
            self.capture_heading = None
        elif tag == "p" and self.in_p:
            self.in_p -= 1
            self.last_paragraph = snippet(" ".join(self.p_parts))
            self.p_parts = []
        elif tag == "figcaption" and self.in_figcaption:
            self.in_figcaption -= 1
            caption = snippet(" ".join(self.caption_parts))
            for idx in self.pending_fig_media:
                self.media[idx]["caption"] = caption
            self.pending_fig_media = []
            self.caption_parts = []
        elif tag == "figure":
            if self.in_figure:
                self.in_figure -= 1
            self.pending_fig_media = []
        elif tag == "video" and self.video_stack:
            self.video_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.capture_heading = (self.capture_heading or "") + data
        if self.in_p:
            self.p_parts.append(data)
        if self.in_figcaption:
            self.caption_parts.append(data)

    def resolve(self, url: str) -> str:
        url = clean_space(url)
        if not url or url.startswith("data:"):
            return ""
        return url_without_fragment(urllib.parse.urljoin(self.source_url, url))

    def position(self) -> str:
        return f"after H2 '{self.heading}'" if self.heading else ""

    def add_media(self, kind: str, origin_url: str, alt: str, caption: str, position_hint: str, from_video: str | None) -> int | None:
        if origin_url in self.seen:
            return None
        self.seen.add(origin_url)
        item = {
            "kind": kind,
            "origin_url": origin_url,
            "alt": snippet(alt),
            "caption": snippet(caption),
            "position_hint": position_hint,
            "from_video": from_video,
        }
        self.media.append(item)
        index = len(self.media) - 1
        if self.in_figcaption == 0 and self.in_figure > 0:
            self.pending_fig_media.append(index)
        return index


def is_video_embed(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return any(h == host or host.endswith("." + h) for h in EMBED_HOSTS)


def discover(source_url: str) -> tuple[dict[str, Any], int]:
    status_id = extract_xcom_status_id(source_url)
    if status_id is not None:
        return discover_via_fxtwitter_api(source_url, status_id)

    fetch_url, mirror = rewrite_for_mirror(source_url)
    result: dict[str, Any] = {
        "source_url": source_url,
        "mirror_used": mirror,
        "fetch_url": fetch_url if mirror else None,
        "fetched": False,
        "media": [],
        "errors": [],
    }
    try:
        text, _ = fetch_text(fetch_url)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result, 1
    parser = MediaHTMLParser(fetch_url)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        result["errors"].append(f"html-parse: {exc}")
    result["fetched"] = True
    result["media"] = parser.media
    return result, 0 if parser.media else 1


def scan_existing_media(kb_root: Path) -> dict[str, dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    for note in kb_root.glob("topics/*/notes/media/*.md"):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        media_id_match = re.search(r"^media_id:\s*['\"]?([0-9a-fA-F]{64})['\"]?\s*$", text, re.M)
        if not media_id_match:
            continue
        path_match = re.search(r"^local_path:\s*['\"]?([^'\"\n]+)['\"]?\s*$", text, re.M)
        found[media_id_match.group(1).lower()] = {
            "local_path": path_match.group(1) if path_match else None,
            "note": str(note),
        }
    return found


def build_local_path(kb_root: Path, kind: str, date: _dt.date, source_slug: str, descriptor: str, media_id: str, ext: str) -> tuple[Path, str]:
    source_slug = slugify(source_slug, "source")
    descriptor = slugify(descriptor, "media")
    if len(source_slug) > 45:
        source_slug = cap_slug(source_slug, 45)
    room = max(1, 60 - len(source_slug) - 1)
    descriptor = cap_slug(descriptor, room)
    slug_part = f"{source_slug}-{descriptor}"
    filename = f"{date.isoformat()}-{slug_part}-{media_id[:8]}{ext}"
    rel_inside = Path("_media") / dir_for_kind(kind) / f"{date:%Y}" / f"{date:%m}" / filename
    return kb_root / rel_inside, str(Path("knowledgebase") / rel_inside)


def capture(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    kb_root = Path(args.kb_root).expanduser().resolve()
    result: dict[str, Any] = {"captured": [], "failed": []}
    existing = scan_existing_media(kb_root)
    today = _dt.date.today()
    remaining = int(args.max_bytes)

    for index, url in enumerate(args.capture):
        if remaining <= 0:
            # Per-source byte budget exhausted by prior captures. Reject this
            # item but keep checking the rest — a smaller item never gets a
            # chance otherwise, and an oversized item must not poison the batch.
            result["failed"].append({"origin_url": url, "reason": "bytes-cap"})
            continue
        try:
            data, content_type, disposition = fetch_bytes(url, remaining)
        except ValueError as exc:
            # Oversized item (e.g. a large animated GIF) exceeded the remaining
            # budget. It stored no bytes, so do not decrement `remaining` and do
            # not cascade the failure onto subsequent URLs — just skip it.
            result["failed"].append({"origin_url": url, "reason": str(exc)})
            continue
        except Exception as exc:
            result["failed"].append({"origin_url": url, "reason": str(exc)})
            continue

        remaining -= len(data)
        media_id = hashlib.sha256(data).hexdigest()
        mime = canonical_mime(content_type, url)
        kind = kind_for(url, mime)
        width, height = image_dimensions(data, mime) if kind == "image" else (None, None)
        descriptor = args.descriptor or basename_descriptor(url, disposition)
        ext = ext_from_url(url)
        if ext not in IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS:
            ext = ext_from_mime(mime)
        if not ext:
            ext = ".bin"

        local_file, local_path = build_local_path(kb_root, kind, today, args.source_slug, descriptor, media_id, ext)
        dedup = existing.get(media_id)
        if dedup:
            local_path = dedup.get("local_path") or local_path
        elif not args.no_write:
            if local_file.exists():
                try:
                    on_disk_hash = hashlib.sha256(local_file.read_bytes()).hexdigest()
                except OSError as exc:
                    result["failed"].append({"origin_url": url, "reason": f"read-existing: {exc}"})
                    continue
                if on_disk_hash != media_id:
                    result["failed"].append({"origin_url": url, "reason": "target-exists-mismatched-hash"})
                    continue
            else:
                local_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = local_file.with_name(local_file.name + ".tmp")
                try:
                    tmp.write_bytes(data)
                    os.replace(tmp, local_file)
                except OSError as exc:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    result["failed"].append({"origin_url": url, "reason": f"write-failed: {exc}"})
                    continue

        result["captured"].append(
            {
                "origin_url": url,
                "kind": kind,
                "media_id": media_id,
                "local_path": local_path,
                "mime": mime,
                "bytes": len(data),
                "width": width,
                "height": height,
                "license": args.license,
                "from_video": args.from_video,
                "dedup_of": dedup.get("local_path") if dedup else None,
            }
        )

    if result["captured"] and result["failed"]:
        return result, 2
    if result["captured"]:
        return result, 0
    return result, 1


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Discover and capture KB media.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover", metavar="URL")
    mode.add_argument("--capture", metavar="URL", nargs="+")
    parser.add_argument("--source-slug")
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    parser.add_argument("--license", default="quoted-with-attribution", choices=sorted(LICENSES))
    parser.add_argument("--from-video")
    parser.add_argument("--descriptor")
    parser.add_argument("--max-bytes", type=int, default=25 * 1024 * 1024)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--filter", choices=["chrome"], action="append", default=[],
                        help="Apply named filters to --discover output. Repeatable. 'chrome' drops items with empty position_hint AND a URL matching known chrome patterns (favicons, footer logos, tracking pixels).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.capture and not args.source_slug:
        parser.error("--source-slug is required with --capture")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")

    if args.discover:
        result, code = discover(args.discover)
        if "chrome" in args.filter and result.get("media"):
            before = len(result["media"])
            result["media"] = [m for m in result["media"] if not is_chrome(m)]
            result["chrome_filtered"] = before - len(result["media"])
    else:
        result, code = capture(args)
    dump(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
