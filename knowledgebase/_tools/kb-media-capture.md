---
author: kb-bot
created: 2026-05-14
modified: 2026-05-14
type: meta
title: kb-media-capture CLI Contract
description: CLI contract for the reusable media discovery and capture tool used by kb-ingest, kb-queue, and kb-auto.
---

# kb-media-capture.py — CLI Contract

A standalone Python script that discovers media on a source page and captures bytes for approved URLs. All KB skills call it; no skill should re-implement HTML parsing or media downloading.

Location: `knowledgebase/_tools/kb-media-capture.py`
Executable: `chmod +x` so it can be invoked directly.
Dependencies: stdlib only (`html.parser`, `urllib`, `hashlib`, `struct`, etc.). `requests` is used opportunistically when available and falls back to `urllib` otherwise — no third-party dep is strictly required.

## Modes

### `--discover <source-url>`

Fetch the source page and enumerate candidate media. Do **not** download any media bytes. Print one JSON object to stdout.

```json
{
  "source_url": "https://example.com/article",
  "fetched": true,
  "media": [
    {
      "kind": "image",
      "origin_url": "https://example.com/hero.png",
      "alt": "alt text from img tag",
      "caption": "nearest figcaption or surrounding paragraph snippet, up to 200 chars",
      "position_hint": "after H2 'Results'",
      "from_video": null
    },
    {
      "kind": "image",
      "origin_url": "https://example.com/poster.jpg",
      "alt": "video poster",
      "caption": "",
      "position_hint": "inside <video> after H2 'Demo'",
      "from_video": "https://example.com/demo.mp4"
    },
    {
      "kind": "video-embed",
      "origin_url": "https://www.youtube.com/embed/abc123",
      "alt": "",
      "caption": "Video: Lecture 3",
      "position_hint": "after H2 'Lecture'",
      "from_video": null
    },
    {
      "kind": "audio",
      "origin_url": "https://example.com/clip.mp3",
      "alt": "",
      "caption": "",
      "position_hint": "after H2 'Recording'",
      "from_video": null
    }
  ],
  "errors": []
}
```

Discovery rules:
- HTML5 `<img src>` → `kind: image`. Resolve relative URLs against the source URL. Skip `data:` URIs.
- HTML5 `<video poster>` → `kind: image` with `from_video` set to the video's `src` (or first `<source>`).
- HTML5 `<video src>` and `<source>` without poster → omit from v1 output (or include with `kind: video` only if the URL ends in a known media extension; default is to omit).
- `<iframe src>` matching YouTube / Vimeo / Twitch URLs → `kind: video-embed`. No thumbnail fetched in v1.
- `<audio src>` and `<a href>` to `.mp3` / `.wav` / `.m4a` → `kind: audio`. No bytes fetched in v1.
- Deduplicate by `origin_url` within the same source.
- If fetch fails, set `fetched: false` and put the reason in `errors`.

Optional discovery flag:
- `--filter chrome` (repeatable): drops items matching known UI-chrome URL patterns (favicons, footer logos, tracking pixels, CMS scaffolding). When applied, the discovery output gains a top-level `chrome_filtered: N` field showing how many items were dropped. Two-tier matching: definite-chrome URL patterns drop on URL alone; probable-chrome patterns drop only when `position_hint` is also empty. See `is_chrome()` in the script for the pattern lists.

### `--capture <url> [<url> ...]`

Download the given media URLs, hash, write to the canonical path, dedup, and emit metadata for note frontmatter.

Required flags:
- `--source-slug <slug>`: the source's slug **without a date prefix** (e.g. `cordaro-x-post-forecast-success`, not `2026-05-14-cordaro-x-post-forecast-success`). The script prepends today's date to construct the filename: `<YYYY-MM-DD>-<source-slug>-<descriptor>-<8char>.<ext>`.
- `--kb-root <path>`: defaults to `$HOME/Vault/knowledgebase`.

Optional flags:
- `--license <posture>`: defaults to `quoted-with-attribution`. Accepted values per Rule 29.
- `--from-video <url>`: associate the captured image with a parent video URL (one per `--capture` invocation, applied to all URLs in that call).
- `--descriptor <slug>`: short descriptor inserted between source slug and short-hash in filename. If omitted, the script derives one from `Content-Disposition` or URL basename, lowercased and sanitized to `[a-z0-9-]`, capped at 60 characters per Rule 29.
- `--max-bytes <N>`: per-source cap, default 25 MB. Capture stops cleanly when exceeded; remaining URLs land in `failed` with reason `bytes-cap`.
- `--no-write`: fetch and validate as normal but skip the disk write. Bytes still travel the network (cap via `--max-bytes`).

Output:

```json
{
  "captured": [
    {
      "origin_url": "https://example.com/hero.png",
      "kind": "image",
      "media_id": "<sha256>",
      "local_path": "knowledgebase/_media/images/2026/05/2026-05-14-source-slug-hero-abcd1234.png",
      "mime": "image/png",
      "bytes": 18393,
      "width": 493,
      "height": 299,
      "license": "quoted-with-attribution",
      "from_video": null,
      "dedup_of": null
    }
  ],
  "failed": [
    { "origin_url": "https://example.com/missing.png", "reason": "404" }
  ]
}
```

Capture rules (per Rule 29):
- Compute SHA-256 of downloaded bytes. Full hash is `media_id`; first 8 chars go in the filename.
- Before writing, scan `topics/*/notes/media/*.md` for an existing `media_id:` match. If found, return that entry as `captured` with `dedup_of: <existing-local-path>` and do not write a duplicate file.
- Filename pattern: `<YYYY-MM-DD>-<source-slug>-<descriptor>-<8char>.<ext>`. Date is today (local time) at first write. Slugs are lowercase ASCII `[a-z0-9-]`, total filename slug portion capped at the Rule 29 60-char limit.
- Directory: `_media/<kind>/<YYYY>/<MM>/`. Create as needed. `kind` is `images` for images, `videos` for videos, `audio` for audio.
- For each captured image, also extract `width` and `height` (Pillow or `imagesize` library; both acceptable).
- Never edit existing files. Refuse to overwrite if a file at the target path already exists with mismatched hash — emit an error and continue.

## Exit codes

- `0` — at least one media item was discovered (in `--discover`) or captured (in `--capture`) successfully.
- `1` — fatal error (could not fetch source, invalid flags, etc.).
- `2` — partial success: some captures failed but at least one succeeded.

## Logging

The script does not write to `_log.md` directly. The calling skill is responsible for appending captures to `_log.md` per Rule 28.

## Example invocations

```bash
# Discovery only
_tools/kb-media-capture.py --discover https://example.com/article

# Capture two approved URLs for a specific source
_tools/kb-media-capture.py --capture \
  https://example.com/hero.png \
  https://example.com/figure-2.png \
  --source-slug example-article \
  --license quoted-with-attribution

# Capture a video poster
_tools/kb-media-capture.py --capture https://example.com/poster.jpg \
  --source-slug lecture-3 \
  --from-video https://example.com/lecture-3.mp4
```
