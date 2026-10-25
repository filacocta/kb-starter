#!/usr/bin/env python3
"""Discover and capture media for a knowledge base."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import importlib.util
import json
import mimetypes
import os
import re
import struct
import sys
import tempfile
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
    """Apply transport identity mirror rewriting. Returns (fetch_url, mirror_used_or_None).

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


FXTWITTER_API_RE = re.compile(r"^/2/thread/(\d+)")


def extract_xcom_status_id(url: str) -> str | None:
    """Return the numeric status ID if `url` is an x.com / twitter.com tweet URL
    (or an already-mirrored fxtwitter / api.fxtwitter.com form), else None."""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    if host == "api.fxtwitter.com":
        m = FXTWITTER_API_RE.match(parts.path)
        return m.group(1) if m else None
    if host in ("fxtwitter.com", "www.fxtwitter.com", "fixupx.com", "vxtwitter.com"):
        m = XCOM_STATUS_RE.match(parts.path)
        return m.group(1) if m else None
    if host not in MIRROR_REWRITES:
        return None
    m = XCOM_STATUS_RE.match(parts.path)
    return m.group(1) if m else None


def discover_via_fxtwitter_api(source_url: str, status_id: str) -> tuple[dict[str, Any], int]:
    """transport identity fast-path for X.com posts. Uses api.fxtwitter.com/2/thread/<id> JSON.

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

    def collect(tweet: dict[str, Any], position: str) -> None:
        media_block = tweet.get("media") or {}
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
                    "caption": (tweet.get("text") or "")[:200],
                    "position_hint": position,
                    "from_video": None,
                })
            elif mtype in ("video", "gif"):
                thumb = m.get("thumbnail_url") or m.get("thumbnailUrl")
                if thumb:
                    result["media"].append({
                        "kind": "image",
                        "origin_url": thumb,
                        "alt": m.get("altText") or "",
                        "caption": (tweet.get("text") or "")[:200],
                        "position_hint": f"{position} (video poster)",
                        "from_video": url,
                    })

    collect(status, "x.com post body")
    quote = status.get("quote")
    if isinstance(quote, dict):
        collect(quote, "x.com quoted post")
    seen_urls: set[str] = set()
    deduped = []
    for m in result["media"]:
        if m["origin_url"] in seen_urls:
            continue
        seen_urls.add(m["origin_url"])
        deduped.append(m)
    result["media"] = deduped
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


class RejectInspectionRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("temporary inspection refuses redirects; approve the new exact URL separately")


def validate_inspection_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.fragment or any(ord(c) <= 32 for c in url)
            or Path(parsed.path).suffix.lower() not in {".mp4", ".webm", ".jpg", ".jpeg", ".png", ".webp"}):
        raise ValueError("temporary inspection requires an exact credential-free HTTPS image/MP4/WebM URL")


def fetch_inspection_bytes(url: str, max_bytes: int) -> tuple[bytes, str, str | None]:
    """One exact request, no redirects/proxies/cookies/netrc or fallback transport.

    The caller supplies the approved URL and an outer wall-clock timeout. The
    socket timeout below is an inactivity bound, not a total-duration guarantee.
    This does not claim network isolation or general-purpose SSRF protection.
    """
    validate_inspection_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectInspectionRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("temporary inspection requires HTTP 200 at the exact requested URL")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise ValueError("bytes-cap")
        chunks, total = [], 0
        while chunk := response.read(min(65536, max_bytes - total + 1)):
            total += len(chunk)
            if total > max_bytes:
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


def is_safe_http_url(url: str) -> bool:
    """Allow only ordinary HTTP(S) URLs suitable for static media references."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            return False
        if not parts.hostname or parts.username is not None or parts.password is not None:
            return False
        if any(char.isspace() for char in url):
            return False
        # Keep discovery conservative: standard HTTP(S) ports only.  This is
        # a URL-shape guard, not a network reachability or authenticity check.
        return parts.port in (None, 80, 443)
    except ValueError:
        return False


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
    The first-comma split is intentionally conservative; callers handle only
    proven same-element URL-prefix duplicates from this extraction.
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
        self.media_index: dict[str, int] = {}
        self.heading: str = ""
        self.capture_heading: str | None = None
        self.in_heading: str | None = None
        self.in_figure = 0
        # Keep figure associations on a stack so a figcaption may legally
        # appear either before or after the media it describes.  The previous
        # flat pending list also made nested figures overwrite one another.
        self.figure_stack: list[dict[str, Any]] = []
        self.in_figcaption = 0
        self.caption_parts: list[str] = []
        self.in_p = 0
        self.p_parts: list[str] = []
        self.last_paragraph = ""
        self.context_candidate = ""
        self.video_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): v or "" for k, v in attrs_list}
        tag = tag.lower()
        # A paragraph is context only when it directly precedes the media (or
        # figure) in the static HTML stream.  Ignore indentation whitespace,
        # but do not carry the paragraph across an unrelated block wrapper.
        if tag == "p":
            self.context_candidate = ""
        elif self.context_candidate and tag not in {
            "figure", "figcaption", "img", "video", "source", "iframe", "audio", "a"
        }:
            self.context_candidate = ""
        elif self.context_candidate and tag == "figcaption" and not self.figure_stack:
            self.context_candidate = ""
        elif self.context_candidate and tag == "source" and not self.video_stack:
            self.context_candidate = ""
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.in_heading = tag.upper()
            self.capture_heading = ""
        elif tag == "figure":
            self.in_figure += 1
            self.figure_stack.append({"media": [], "caption": ""})
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
            direct_candidates = [
                attrs.get("src", ""),
                attrs.get("data-src", ""),
                attrs.get("data-original", ""),
            ]
            # _first_url_from_srcset naively splits at the first comma. Some
            # image-proxy URLs (notably Substack's CDN) contain literal commas,
            # so that split can yield a prefix such as `.../$s_!YptM!`.
            # Suppress only when the raw same-element srcset value also
            # contains the full usable direct URL; a prefix coincidence alone
            # is not enough to discard a legitimate alternate.
            direct_urls = {
                resolved
                for candidate in direct_candidates
                if (resolved := self.resolve(candidate))
            }
            srcset_values = [attrs.get("data-srcset", ""), attrs.get("srcset", "")]
            srcset_candidates = [_first_url_from_srcset(value) for value in srcset_values]
            candidates = direct_candidates + srcset_candidates
            alt = attrs.get("alt", "")
            seen_local: set[str] = set()
            for index, cand in enumerate(candidates):
                resolved = self.resolve(cand)
                if resolved and resolved not in seen_local:
                    srcset_index = index - len(direct_candidates)
                    raw_srcset = (
                        srcset_values[srcset_index].lstrip()
                        if 0 <= srcset_index < len(srcset_values) else ""
                    )
                    if (srcset_index >= 0
                            and any(
                                direct.startswith(resolved + ",")
                                and raw_srcset.startswith(direct)
                                and (
                                    len(raw_srcset) == len(direct)
                                    or raw_srcset[len(direct)] in ", \t\r\n"
                                )
                                for direct in direct_urls
                            )):
                        continue
                    seen_local.add(resolved)
                    self.add_media("image", resolved, alt, "", self.position(), None, self.context_candidate)
            if not self.figure_stack:
                self.context_candidate = ""
        elif tag == "video":
            video_src = self.resolve(attrs.get("src", ""))
            poster = self.resolve(attrs.get("poster", ""))
            video_item: dict[str, Any] = {
                "src": video_src,
                "poster": poster,
                "poster_index": None,
                "video_urls": set(),
                "video_indices": {},
                "parent_video_url": None,
            }
            if poster:
                video_item["poster_index"] = self.add_media(
                    "image",
                    poster,
                    attrs.get("alt", "video poster"),
                    "",
                    f"inside <video> {self.position()}",
                    None,
                    self.context_candidate,
                )
            if video_src:
                self.add_video_source(video_item, video_src, attrs.get("type", ""), attrs.get("alt", ""))
            self.video_stack.append(video_item)
            if not self.figure_stack:
                self.context_candidate = ""
        elif tag == "source" and self.video_stack:
            src = self.resolve(attrs.get("src", ""))
            current = self.video_stack[-1]
            if src and not current.get("src"):
                current["src"] = src
            if src:
                self.add_video_source(current, src, attrs.get("type", ""), "")
        elif tag == "iframe":
            # Lazy embeds commonly put a blank/placeholder src alongside the
            # real URL in data-src.  Resolve both without executing JS and
            # retain only the established supported embed hosts.
            candidates = [attrs.get("src", ""), attrs.get("data-src", "")]
            seen_local: set[str] = set()
            label = snippet(attrs.get("title", ""))
            for cand in candidates:
                origin = self.resolve(cand)
                if origin and origin not in seen_local and is_video_embed(origin):
                    seen_local.add(origin)
                    index = self.add_media(
                        "video-embed", origin, "", "", self.position(), None, self.context_candidate
                    )
                    if index is not None and label:
                        # A title is observed iframe metadata, not a caption.
                        self.fill_metadata(self.media[index], title=label)
            if not self.figure_stack:
                self.context_candidate = ""
        elif tag == "audio":
            origin = self.resolve(attrs.get("src", ""))
            if origin:
                self.add_media("audio", origin, "", "", self.position(), None, self.context_candidate)
            if not self.figure_stack:
                self.context_candidate = ""
        elif tag == "a":
            origin = self.resolve(attrs.get("href", ""))
            if origin and ext_from_url(origin) in AUDIO_EXTS:
                self.add_media("audio", origin, "", "", self.position(), None, self.context_candidate)
            if not self.figure_stack:
                self.context_candidate = ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.in_heading and tag == self.in_heading.lower():
            self.heading = snippet(self.capture_heading or "", 80)
            self.in_heading = None
            self.capture_heading = None
        elif tag == "p" and self.in_p:
            self.in_p -= 1
            self.last_paragraph = snippet(" ".join(self.p_parts))
            self.context_candidate = self.last_paragraph
            self.p_parts = []
        elif tag == "figcaption" and self.in_figcaption:
            self.in_figcaption -= 1
            caption = snippet(" ".join(self.caption_parts))
            if self.figure_stack:
                figure = self.figure_stack[-1]
                figure["caption"] = caption
                for idx in figure["media"]:
                    self.fill_metadata(self.media[idx], caption=caption)
            self.caption_parts = []
        elif tag == "figure":
            if self.in_figure:
                self.in_figure -= 1
            if self.figure_stack:
                self.figure_stack.pop()
            if not self.figure_stack:
                self.context_candidate = ""
        elif tag == "video" and self.video_stack:
            self.video_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.context_candidate and data.strip() and not self.in_p and not self.in_figcaption:
            self.context_candidate = ""
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
        try:
            resolved = urllib.parse.urljoin(self.source_url, url)
            parts = urllib.parse.urlsplit(resolved)
            if not is_safe_http_url(resolved):
                return ""
            # Accessing .port validates malformed ports as well as enforcing
            # the conservative standard HTTP(S) port policy.
            if parts.port not in (None, 80, 443):
                return ""
        except ValueError:
            return ""
        return url_without_fragment(resolved)

    def position(self) -> str:
        return f"after H2 '{self.heading}'" if self.heading else ""

    def add_video_source(
        self,
        video_item: dict[str, Any],
        origin_url: str,
        media_type: str,
        alt: str,
    ) -> int | None:
        """Add a recognized video URL and associate the first one to its poster."""
        if not self.is_video_source(origin_url, media_type):
            return None
        video_urls: set[str] = video_item["video_urls"]
        if origin_url in video_urls:
            return video_item["video_indices"].get(origin_url)
        video_urls.add(origin_url)
        index = self.add_media(
            "video",
            origin_url,
            alt,
            "",
            f"inside <video> {self.position()}",
            None,
            None,
        )
        video_item["video_indices"][origin_url] = index
        if video_item.get("parent_video_url") is None:
            video_item["parent_video_url"] = origin_url
            poster_index = video_item.get("poster_index")
            if poster_index is not None:
                self.fill_metadata(self.media[poster_index], from_video=origin_url)
        return index

    @staticmethod
    def is_video_source(url: str, media_type: str = "") -> bool:
        """Recognize direct HTML5 video URLs without downloading them.

        A declared video MIME type is sufficient for extensionless URLs.  If
        a type is present but explicitly names another media family, do not
        infer video from a misleading filename.
        """
        if not is_safe_http_url(url):
            return False
        normalized_type = media_type.split(";", 1)[0].strip().lower()
        if normalized_type:
            if not normalized_type.startswith("video/"):
                return False
            # Do not let an explicit video type turn an obviously different
            # media filename into a video.  Conversely, a video MIME type may
            # legitimately describe an extensionless URL.
            return ext_from_url(url) not in IMAGE_EXTS | AUDIO_EXTS
        return ext_from_url(url) in VIDEO_EXTS

    def add_media(
        self,
        kind: str,
        origin_url: str,
        alt: str,
        caption: str,
        position_hint: str,
        from_video: str | None,
        context: str | None = None,
    ) -> int | None:
        existing_index = self.media_index.get(origin_url)
        if existing_index is not None:
            self.fill_metadata(
                self.media[existing_index],
                alt=alt,
                caption=caption,
                position_hint=position_hint,
                from_video=from_video,
                context=context,
            )
            self.associate_figure(existing_index)
            return existing_index
        self.seen.add(origin_url)
        item = {
            "kind": kind,
            "origin_url": origin_url,
            "alt": snippet(alt),
            "caption": snippet(caption),
            "position_hint": position_hint,
            "from_video": from_video,
        }
        self.fill_metadata(item, context=context)
        self.media.append(item)
        index = len(self.media) - 1
        self.media_index[origin_url] = index
        self.associate_figure(index)
        return index

    def associate_figure(self, index: int) -> None:
        if not self.figure_stack:
            return
        figure = self.figure_stack[-1]
        if index not in figure["media"]:
            figure["media"].append(index)
        if figure["caption"]:
            self.fill_metadata(self.media[index], caption=figure["caption"])

    @staticmethod
    def fill_metadata(item: dict[str, Any], **values: str | None) -> None:
        """Fill only absent observed metadata; never replace first provenance."""
        for field, value in values.items():
            # Resolved URLs are identity/provenance values, not descriptive
            # prose.  Keep signed URLs and long query strings byte-for-byte;
            # only human-readable fields receive the snippet limit.
            if field == "from_video":
                cleaned = value or ""
            else:
                cleaned = snippet(value or "")
            if cleaned and not item.get(field):
                item[field] = cleaned


def is_video_embed(url: str) -> bool:
    if not is_safe_http_url(url):
        return False
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
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


def scan_existing_media(kb_root: Path, media_id: str, data: bytes,
                        limit: int, warnings: list[dict]) -> dict | None:
    """Find one trustworthy description and verify its actual retained bytes.

    Unrelated malformed legacy metadata is reported, not repaired. A malformed
    record claiming this hash, or multiple matching records, stops this item.
    """
    matches = []
    for note in sorted(kb_root.glob("topics/*/notes/media/*.md")):
        text = media_guard().regular_text(note)
        header = text.split("\n---", 1)[0]
        try:
            metadata = media_guard().frontmatter_metadata(text, note)
        except ValueError as exc:
            if media_id in header.lower():
                raise ValueError(f"matching media record has invalid metadata: {note}") from exc
            warning = {"note": str(note), "reason": "unrelated invalid media metadata; not used for dedup"}
            if warning not in warnings:
                warnings.append(warning)
            continue
        declared_id = metadata.get("media_id")
        if not isinstance(declared_id, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", declared_id):
            if media_id in header.lower():
                raise ValueError(f"matching media_id must be a full SHA-256 string: {note}")
            continue
        if declared_id.lower() != media_id:
            continue
        if (metadata.get("author") != "kb-bot"
                or metadata.get("type") != "media-description"
                or metadata.get("media_status") != "captured"):
            raise ValueError(f"matching media record is not an owned captured description: {note}")
        matches.append((note, metadata))
    if len(matches) > 1:
        raise ValueError("ambiguous media identity: multiple description notes claim this hash")
    if not matches:
        return None
    note, metadata = matches[0]
    path, error = resolve_media_target(kb_root, metadata.get("local_path", ""))
    if error:
        raise ValueError(f"invalid dedup destination in {note}: {error}")
    retained, _ = checked_media_bytes(path, limit)
    if retained != data or hashlib.sha256(retained).hexdigest() != media_id:
        raise ValueError(f"dedup media bytes do not match: {note}")
    return {"local_path": "knowledgebase/" + path.relative_to(kb_root).as_posix(), "note": str(note)}


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


def build_staged_path(stage_dir: Path, media_id: str, ext: str) -> Path:
    return stage_dir / f"{media_id}{ext}"


def resolve_stage_dir(kb_root: Path, value: str) -> Path:
    """New staging writes stay in the KB's dedicated media staging area."""
    if not value or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("invalid stage-dir")
    requested = Path(value).expanduser()
    if ".." in requested.parts or "\\" in value:
        raise ValueError("stage-dir must not contain traversal")
    if not requested.is_absolute():
        requested = kb_root / requested
    relative = requested.relative_to(kb_root / "_tools/staging/media")
    if requested.resolve(strict=False) != requested:
        raise ValueError("stage-dir must not contain symlinks")
    cursor = kb_root
    for part in requested.relative_to(kb_root).parts:
        cursor /= part
        if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
            raise ValueError("stage-dir must contain only real directories")
    return kb_root / "_tools/staging/media" / relative


def resolve_media_target(kb_root: Path, target_path: str) -> tuple[Path | None, str | None]:
    try:
        root = media_guard().canonical_root(kb_root)
        if not isinstance(target_path, str) or not target_path or target_path != target_path.strip():
            raise ValueError("target-path must be nonempty without surrounding whitespace")
        logical = target_path.removeprefix("knowledgebase/")
        path = Path(logical)
        if (path.is_absolute() or ".." in path.parts or "\\" in logical
                or path.as_posix() != logical or any(ord(c) < 32 for c in logical)):
            raise ValueError("target-path must be canonical without traversal or control characters")
        if (len(path.parts) < 3 or path.parts[0] != "_media"
                or path.parts[1] not in {"images", "videos", "audio"}
                or path.suffix.lower() not in IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS | {".bin"}):
            raise ValueError("target-path must name binary media under _media/images, videos or audio")
        target = root / path
        if target.resolve(strict=False) != target:
            raise ValueError("target-path must not contain symlinks")
        # Resolve catches existing symlink parents; check dangling links too.
        cursor = root
        for part in path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("target-path must not contain symlinks")
        return target, None
    except (OSError, ValueError, RuntimeError) as exc:
        return None, str(exc)


def media_guard():
    key = "kb_media_write_guard"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-write-guard.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def checked_media_bytes(path: Path, limit: int) -> tuple[bytes, tuple]:
    """Bounded descriptor read; no links, special files or silent read changes."""
    descriptor, before = media_guard()._open_regular(path, flags=os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        if before.st_size < 1 or before.st_size > limit:
            raise ValueError("media must contain 1 byte through --max-bytes")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
    if identity(before) != identity(after):
        raise ValueError("media changed during read")
    if not data or len(data) > limit:
        raise ValueError("media must contain 1 byte through --max-bytes")
    return data, identity(after)


def create_media_parents(root: Path, target: Path) -> None:
    """Create only validated destination directories while holding the KB lock."""
    cursor = root
    for part in target.parent.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink media directory")
        cursor.mkdir(exist_ok=True)
        media_guard()._walk_no_symlinks(cursor)


def publish_media_bytes(target: Path, data: bytes) -> None:
    """Locked caller only: unique temporary + atomic no-overwrite publication."""
    descriptor, name = tempfile.mkstemp(prefix=".kb-media-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        media_guard()._walk_no_symlinks(target, allow_final_missing=True)
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def commit_staged(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {"committed": [], "failed": []}
    publication_attempted = False
    staged_path = Path(args.commit_staged).expanduser().absolute()
    try:
        kb_root = media_guard().canonical_root(Path(args.kb_root).expanduser())
        if type(args.max_bytes) is not int or args.max_bytes < 1:
            raise ValueError("--max-bytes must be positive")
        expected_id = args.media_id or ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected_id):
            raise ValueError("--media-id requires the full lowercase SHA-256")
        target_file, error = resolve_media_target(kb_root, args.target_path or "")
        if error:
            raise ValueError(error)
        if staged_path == target_file:
            raise ValueError("staging input must differ from destination")
        staged_data, identity = checked_media_bytes(staged_path, args.max_bytes)
        if hashlib.sha256(staged_data).hexdigest() != expected_id:
            raise ValueError("media-id-mismatch")

        def existing_target() -> bool:
            # Missing is the only permissible read failure for a new target.
            try:
                data, _ = checked_media_bytes(target_file, args.max_bytes)
            except FileNotFoundError:
                return False
            if data != staged_data:
                raise ValueError("target-exists-mismatched-hash")
            return True

        already_present = existing_target()
        item = {"staged_path": str(staged_path),
                "local_path": "knowledgebase/" + target_file.relative_to(kb_root).as_posix(),
                "target_path": "knowledgebase/" + target_file.relative_to(kb_root).as_posix(),
                "media_id": expected_id, "bytes": len(staged_data),
                "committed": False, "already_present": already_present,
                "staged_preserved": True}
        if args.no_write:
            result["planned"] = [item]
            return result, 0
        with media_guard().write_lock(kb_root, nonblocking=True):
            locked_target, error = resolve_media_target(kb_root, args.target_path)
            if error or locked_target != target_file:
                raise ValueError(error or "destination changed")
            current_data, current_identity = checked_media_bytes(staged_path, args.max_bytes)
            if current_identity != identity or current_data != staged_data:
                raise ValueError("staging input changed before commit")
            already_present = existing_target()
            if not already_present:
                create_media_parents(kb_root, target_file)
                publication_attempted = True
                publish_media_bytes(target_file, staged_data)
            # Report success only after reading back the published bytes.
            if not existing_target():
                raise ValueError("destination missing after publication")
            item.update(committed=True, already_present=already_present)
            result["committed"].append(item)
        return result, 0
    except (OSError, ValueError, RuntimeError) as exc:
        result["failed"].append({"staged_path": str(staged_path), "reason": str(exc),
                                 "publication_may_have_succeeded": publication_attempted,
                                 "resume": "Inspect destination; retry the preserved staging input and exact hash."})
        return result, 1


def capture(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {"captured": [], "failed": [], "warnings": []}
    today = _dt.date.today()
    try:
        kb_root = media_guard().canonical_root(Path(args.kb_root).expanduser())
        if type(args.max_bytes) is not int or args.max_bytes < 1:
            raise ValueError("--max-bytes must be positive")
        if args.license == "do-not-redistribute":
            raise ValueError("license posture forbids media capture; retain the URL only")
        stage_dir = resolve_stage_dir(kb_root, args.stage_dir) if args.stage_dir else None
        if getattr(args, "temporary_inspection", False):
            if args.no_write:
                raise ValueError("temporary inspection requires staging; --no-write is not supported")
            if stage_dir is None or args.max_bytes > 25 * 1024 * 1024:
                raise ValueError("temporary inspection requires --stage-dir and --max-bytes at most 25 MiB")
            if len(set(args.capture)) != len(args.capture):
                raise ValueError("temporary inspection refuses duplicate URLs before fetching")
            for url in args.capture:
                validate_inspection_url(url)
        expected_id = getattr(args, "expected_media_id", None)
        if expected_id is not None:
            if (not getattr(args, "temporary_inspection", False) or len(args.capture) != 1
                    or re.fullmatch(r"[0-9a-f]{64}", expected_id) is None):
                raise ValueError("--expected-media-id requires one temporary-inspection URL and a full lowercase SHA-256")
        # Reject known unsafe destination parents before any download.
        for kind in ("image", "video", "audio"):
            _, preview_path = build_local_path(kb_root, kind, today, args.source_slug,
                                               args.descriptor or "media", "0" * 64, ".bin")
            _, error = resolve_media_target(kb_root, preview_path)
            if error:
                raise ValueError(error)
    except (OSError, ValueError, RuntimeError) as exc:
        result["failed"].append({"origin_url": None, "reason": str(exc)})
        return result, 1
    remaining = args.max_bytes

    for url in args.capture:
        publication_attempted = False
        write_target = None
        if remaining <= 0:
            # Per-source byte budget exhausted by prior captures. Reject this
            # item but keep checking the rest — a smaller item never gets a
            # chance otherwise, and an oversized item must not poison the batch.
            result["failed"].append({"origin_url": url, "reason": "bytes-cap"})
            continue
        try:
            if getattr(args, "temporary_inspection", False):
                data, content_type, disposition = fetch_inspection_bytes(url, remaining)
            else:
                data, content_type, disposition = fetch_bytes(url, remaining)
            if not data or len(data) > remaining:
                raise ValueError("empty media or bytes-cap")
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
        if expected_id is not None and media_id != expected_id:
            result["failed"].append({"origin_url": url, "reason": "expected-media-id-mismatch",
                                     "media_id": media_id, "expected_media_id": expected_id})
            continue
        mime = canonical_mime(content_type, url)
        kind = kind_for(url, mime)
        width, height = image_dimensions(data, mime) if kind == "image" else (None, None)
        descriptor = args.descriptor or basename_descriptor(url, disposition)
        ext = ext_from_url(url)
        if ext not in IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS:
            ext = ext_from_mime(mime)
        if ext not in IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS:
            ext = ".bin"

        local_file, local_path = build_local_path(kb_root, kind, today, args.source_slug, descriptor, media_id, ext)
        staged_path: Path | None = None
        committed = False
        already_present = False
        dedup = None

        def inspect_destinations():
            nonlocal staged_path, write_target, local_path, dedup, committed, already_present
            target, error = resolve_media_target(kb_root, local_path)
            if error or target != local_file:
                raise ValueError(error or "media destination changed")
            if stage_dir is not None:
                current_stage = resolve_stage_dir(kb_root, args.stage_dir)
                if current_stage != stage_dir:
                    raise ValueError("staging destination changed")
            dedup = scan_existing_media(kb_root, media_id, data, args.max_bytes, result["warnings"])
            if dedup is not None:
                local_path = dedup["local_path"]
                committed = True
                already_present = True
                return
            staged_path = build_staged_path(stage_dir, media_id, ext) if stage_dir is not None else None
            write_target = staged_path or local_file
            try:
                retained, _ = checked_media_bytes(write_target, args.max_bytes)
            except FileNotFoundError:
                return
            if retained != data:
                raise ValueError("target-exists-mismatched-hash")
            already_present = True
            committed = staged_path is None

        try:
            if args.no_write:
                # Capture preview still fetches, but never creates a lock/dir/file.
                inspect_destinations()
            else:
                # Network work is finished. Re-read dedup and all destinations
                # under the lock rather than using a stale pre-fetch index.
                # Temporary inspection keeps already-fetched bytes in memory
                # through brief writer contention. The mandatory caller wall
                # timeout also bounds this wait; no lock stealing or re-fetch.
                with media_guard().write_lock(kb_root, nonblocking=not getattr(args, "temporary_inspection", False)):
                    inspect_destinations()
                    if dedup is None:
                        if not already_present:
                            create_media_parents(kb_root, write_target)
                            publication_attempted = True
                            publish_media_bytes(write_target, data)
                        retained, _ = checked_media_bytes(write_target, args.max_bytes)
                        if retained != data:
                            raise ValueError("media changed after publication")
                        committed = staged_path is None
        except (OSError, ValueError, RuntimeError) as exc:
            result["failed"].append({"origin_url": url, "reason": str(exc),
                "target_path": str(write_target) if write_target else local_path,
                "media_id": media_id,
                "publication_may_have_succeeded": publication_attempted,
                "resume": "Inspect the destination and retry identical bytes; no existing file was overwritten."})
            continue

        item = {
                "origin_url": url,
                "kind": kind,
                "media_id": media_id,
                "local_path": local_path,
                "target_path": local_path,
                "mime": mime,
                "bytes": len(data),
                "width": width,
                "height": height,
                "license": args.license,
                "from_video": args.from_video,
                "dedup_of": dedup.get("local_path") if dedup else None,
                "committed": committed,
                "already_present": already_present,
            }
        if staged_path is not None:
            item["staged_path"] = str(staged_path)
        result["captured"].append(item)

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
    mode.add_argument("--commit-staged", metavar="PATH")
    parser.add_argument("--source-slug")
    parser.add_argument("--target-path")
    parser.add_argument("--media-id")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--license", default="quoted-with-attribution", choices=sorted(LICENSES))
    parser.add_argument("--from-video")
    parser.add_argument("--descriptor")
    parser.add_argument("--max-bytes", type=int, default=25 * 1024 * 1024)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--temporary-inspection", action="store_true",
                        help="Exact HTTPS image/MP4/WebM staging only; no redirects, proxy, auth or retry. "
                        "Requires --stage-dir, <=25 MiB and caller-supplied wall timeout; not approval itself.")
    parser.add_argument("--expected-media-id", help="Single temporary asset must match this previously observed SHA-256 before staging.")
    parser.add_argument(
        "--stage-dir",
        help="Stage captured bytes under KB-root/_tools/staging/media instead of final _media paths. "
        "JSON still reports target_path/local_path for the eventual committed location.",
    )
    parser.add_argument("--filter", choices=["chrome"], action="append", default=[],
                        help="Apply named filters to --discover output. Repeatable. 'chrome' drops items with empty position_hint AND a URL matching known chrome patterns (favicons, footer logos, tracking pixels).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.capture and not args.source_slug:
        parser.error("--source-slug is required with --capture")
    if args.temporary_inspection and not args.capture:
        parser.error("--temporary-inspection is only valid with --capture")
    if args.expected_media_id and not args.temporary_inspection:
        parser.error("--expected-media-id requires --temporary-inspection")
    if args.commit_staged and not args.target_path:
        parser.error("--target-path is required with --commit-staged")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")

    if args.discover:
        result, code = discover(args.discover)
        if "chrome" in args.filter and result.get("media"):
            before = len(result["media"])
            result["media"] = [m for m in result["media"] if not is_chrome(m)]
            result["chrome_filtered"] = before - len(result["media"])
    elif args.capture:
        result, code = capture(args)
    else:
        result, code = commit_staged(args)
    dump(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
