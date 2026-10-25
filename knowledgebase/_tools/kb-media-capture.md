---
author: kb-bot
created: 2026-05-14
modified: 2026-09-11
type: meta
title: kb-media-capture CLI Contract
description: CLI contract for media discovery and capture used by kb-ingest and the internal kb-auto worker.
---

# kb-media-capture.py — CLI Contract

A standalone Python script that discovers media on a source page and captures
bytes for approved URLs. Its normal capture mode and its explicitly guarded
temporary-inspection mode are separate contracts. All KB skills call it; no
skill should re-implement HTML parsing or media downloading.

Location: `knowledgebase/_tools/kb-media-capture.py`
Executable: `chmod +x` so it can be invoked directly.
Dependencies: discovery uses stdlib and optionally `requests`; capture and staged finalization also use the shared KB write guard and its PyYAML dependency.

September 8 scope: direct capture, staging writes and finalization use checked
paths, shared locking and no-overwrite publication. Dedup verifies actual bytes
instead of trusting a note's path alone. This establishes byte custody, not
image authenticity, source accuracy or completed interpretation.
Ordinary `--capture --no-write` still fetches and needs approval; no timer is
enabled. `--temporary-inspection --no-write` is rejected before fetching:
temporary inspection requires real staging, not a nonexistent staged-path result.
Temporary inspection requires a finite wall-clock limit supplied by its caller;
the helper's 30-second socket timeout is only an inactivity bound.

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
      "caption": "text of an associated figcaption, up to 200 chars; otherwise empty",
      "context": "optional immediately preceding paragraph; context only, not a caption",
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
      "kind": "video",
      "origin_url": "https://example.com/demo.mp4",
      "alt": "",
      "caption": "",
      "position_hint": "inside <video> after H2 'Demo'",
      "from_video": null
    },
    {
      "kind": "video-embed",
      "origin_url": "https://www.youtube.com/embed/abc123",
      "alt": "",
      "caption": "",
      "title": "Lecture 3",
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

Static-HTML discovery rules (September 10):
- HTML5 `<img src>` and supported lazy/srcset attributes → `kind: image`. Resolve relative URLs against the source URL. Static references must be HTTP(S), without credentials, with no explicit port or port 80/443. Reject unsupported schemes, malformed ports and nonstandard ports. This is a URL-shape filter, not a network-isolation or authenticity guarantee.
- When the helper's naïve first-comma split yields a `srcset`/`data-srcset` candidate that is the exact comma-prefix of a usable same-element `src`, `data-src`, or `data-original` URL, and that full direct URL begins the raw same-element source-set value as a complete token, the candidate is suppressed as a parser-generated duplicate (for example, a Substack CDN transform URL containing literal commas). Prefix coincidence alone, including a direct URL appearing only in a later candidate, is retained; this narrow guard does not broadly change srcset parsing, and a srcset-only proxy URL may still need separate handling.
- `caption` contains only an associated `<figcaption>` in this parser. Captions before or after media are supported within the containing figure. Optional `context` is bounded nearby paragraph text, consumed/cleared at media or unrelated block boundaries; proximity does not establish a semantic caption. Optional iframe `title` is separate observed metadata. Descriptive fields are capped at 200 characters; URL identity fields are not truncated.
- HTML5 `<video poster>` → `kind: image`; `from_video` is the first recognized video source, not merely the first child URL. Unsupported or contradictory source types do not establish a video-parent link.
- HTML5 `<video src>` and nested `<source>` → `kind: video` for known video extensions or a declared `video/*` type, including extensionless endpoints. Explicit conflicting image/audio evidence is conservatively excluded. Posterless video is supported; unresolved extensionless/untyped streams can still be missed.
- `<iframe src>` and lazy `<iframe data-src>` on the existing YouTube / Vimeo / Twitch embed-host allowlist → `kind: video-embed`. Check parsed hostname boundaries, not lookalike text. No JavaScript is executed and no thumbnail or video bytes are fetched.
- `<audio src>` and `<a href>` to `.mp3` / `.wav` / `.m4a` → `kind: audio`. No bytes fetched in v1.
- Deduplicate by `origin_url` within the same source. Later occurrences may fill empty observed metadata, including captions or poster-parent links; they never overwrite the first nonempty value. This is not a complete inventory of conflicting captions or repeated placements.
- If fetch fails, set `fetched: false` and put the reason in `errors`.

The example and caption guarantees above describe `MediaHTMLParser`, not every
transport. FxTwitter discovery still supplies tweet context in its legacy
`caption` field. The headless sibling has a separate extraction path and still
uses iframe titles as captions. Do not treat either as observed figcaptions.
`kb-prep-item.py` passes discovery fields through; agents must preserve these
provenance distinctions. Discovery establishes neither complete media coverage
nor inspected image/video content. The internal `video-embed` label must become
schema-valid `kind: video` when writing a reviewed source-media manifest; it is
not a new frontmatter enum or permission to fetch the embedded material.

Optional discovery flag:
- `--filter chrome` (repeatable): drops items matching known UI-chrome URL patterns (favicons, footer logos, tracking pixels, CMS scaffolding). When applied, the discovery output gains a top-level `chrome_filtered: N` field showing how many items were dropped. Two-tier matching: definite-chrome URL patterns drop on URL alone; probable-chrome patterns drop only when `position_hint` is also empty. See `is_chrome()` in the script for the pattern lists.

### `--capture <url> [<url> ...]`

Download the given media URLs, hash, write to the canonical path, dedup, and emit metadata for note frontmatter.

Required flags:
- `--source-slug <slug>`: the source's slug **without a date prefix** (e.g. `cordaro-x-post-forecast-success`, not `2026-05-14-cordaro-x-post-forecast-success`). The script prepends today's date to construct the filename: `<YYYY-MM-DD>-<source-slug>-<descriptor>-<8char>.<ext>`.
- `--kb-root <path>`: defaults to `<kb-root>`.

Optional flags:
- `--license <posture>`: defaults to `quoted-with-attribution`. Accepted values per media capture contract. `do-not-redistribute` refuses capture before fetching; retain the URL instead.
- `--from-video <url>`: associate the captured image with a parent video URL (one per `--capture` invocation, applied to all URLs in that call).
- `--descriptor <slug>`: short descriptor inserted between source slug and short-hash in filename. If omitted, the script derives one from `Content-Disposition` or URL basename, lowercased and sanitized to `[a-z0-9-]`, capped at 60 characters per media capture contract.
- `--max-bytes <N>`: aggregate cap for this capture invocation (default 25 MiB). Capture stops cleanly when exceeded; remaining URLs land in `failed` with reason `bytes-cap`.
- `--no-write`: fetch and validate as normal but skip the disk write. Bytes still travel the network (cap via `--max-bytes`).
- `--stage-dir <path>`: stage new bytes under `<kb-root>/_tools/staging/media/` (optionally a subdirectory), instead of the final `_media/` path. Relative paths resolve from the KB root. External paths such as the old `/tmp` examples now fail before fetching. The JSON reports the eventual `local_path` / `target_path` and adds `staged_path` plus `committed: false`. Verified existing dedup bytes may be reused without staging.
- `--temporary-inspection`: explicit interactive media capture contract exception. Requires `--stage-dir`, `--max-bytes <= 25 MiB`, and a complete exact user-approved capture list. Duplicate URL strings are refused before fetch. It accepts only credential-free HTTPS image/MP4/WebM URLs and performs one request per URL per invocation with no redirect, proxy, cookie, login/session, DRM, automatic retry, mirror or unlisted-variant path. This flag is not approval and is never a headless/autonomous mode.
- `--expected-media-id`: one temporary-inspection URL must match this full lowercase SHA-256 before staging. Used only for the explicitly reviewed local-staging recovery described in media capture contract; it grants no new fetch or retry authority. Mismatched bytes are reported and not written.

Output:

```json
{
  "captured": [
    {
      "origin_url": "https://example.com/hero.png",
      "kind": "image",
      "media_id": "<sha256>",
      "local_path": "knowledgebase/_media/images/2026/05/2026-05-14-source-slug-hero-abcd1234.png",
      "target_path": "knowledgebase/_media/images/2026/05/2026-05-14-source-slug-hero-abcd1234.png",
      "mime": "image/png",
      "bytes": 18393,
      "width": 493,
      "height": 299,
      "license": "quoted-with-attribution",
      "from_video": null,
      "dedup_of": null,
      "committed": true
    }
  ],
  "failed": [
    { "origin_url": "https://example.com/missing.png", "reason": "404" }
  ]
}
```

Capture rules (per media capture contract):
- Compute SHA-256 of downloaded bytes. Full hash is `media_id`; first 8 chars go in the filename.
- Before writing, scan `topics/*/notes/media/*.md` under the shared lock for the downloaded hash. Reuse only a unique, owned captured description whose canonical path contains identical checked bytes. Missing/mismatched/linked files and ambiguous matching records fail that item. Unrelated malformed metadata is reported in `warnings`, not trusted or repaired; unsafe note reads fail closed.
- Filename pattern: `<YYYY-MM-DD>-<source-slug>-<descriptor>-<8char>.<ext>`. Date is today (local time) at first write. Slugs are lowercase ASCII `[a-z0-9-]`, total filename slug portion capped at the media capture contract 60-char limit.
- Directory: `_media/<kind>/<YYYY>/<MM>/`. Create as needed. `kind` is `images` for images, `videos` for videos, `audio` for audio.
- Staging mode: with `--stage-dir`, new bytes are written to `staged_path`; finalization copies them through `--commit-staged`. Preserve unresolved staging inputs; cleanup is a separately scoped action, not automatic failure handling.
- For each captured image, also extract `width` and `height` (Pillow or `imagesize` library; both acceptable).
- Never edit existing files. Refuse to overwrite if a file at the target path already exists with mismatched hash — emit an error and continue.
- Download outside the lock; revalidate destinations and dedup inside it. Direct
  and staging writes use unique temporary files and atomic no-overwrite linking,
  followed by byte readback. Identical existing files return `already_present`.
  Failures after a publication attempt report `publication_may_have_succeeded`
  with the destination/hash; inspect and retry rather than assuming rollback.
- Temporary-inspection staging waits for a busy shared lock within the mandatory
  caller wall-clock deadline; ordinary capture retains its nonblocking behavior.
  A caller timeout is a terminal tooling state, not an automatic retry. A capture
  result establishes byte custody, not media authenticity: native viewing or the
  local decoder must succeed before an inspected-content claim. A verified
  canonical dedup result is not fresh staging and must never enter scratch cleanup.
- Capture `--no-write` creates no lock, directories or files. It may report
  `committed: true` only for already-present verified bytes, never new bytes.
  It is a point-in-time read, not a reserved destination or snapshot guarantee.

### `--temporary-inspection`

This mode is for a finite, interactive review whose exact URLs are already
listed in the caller's run manifest and explicitly approved by the user. Pass
the exact image/MP4/WebM URLs as the complete `--capture` list, a fresh private
ignored subdirectory such as `_tools/staging/media/<run-id>`, and an invocation
budget no greater than 25 MiB. The capture code refuses userinfo, non-HTTPS
URLs, nonstandard ports, URL fragments, unsupported suffixes and redirects;
it disables proxy use and supplies no credentials. It does not make HLS/
playlist, iframe, mirror or rendition-discovery requests. The caller must
enforce a finite wall-clock deadline (the implementation's 30-second socket
timeout is not a total-run timeout).

The result is staged custody only. Do not use `--commit-staged` for a temporary
inspection asset unless a separate retention decision permits canonical
`_media/` storage. When the source note is authored after inspection, a
non-retained asset is `status: not-captured` with
`not_captured_reason: "temporarily inspected; not retained"`; include known
hash/MIME/size/dimensions/duration but no `path` or `captured` date. The
staged `local_path`/`target_path` fields describe a possible future destination,
not retained media. Pass the staged path and full SHA-256 only to the local-only
`kb-media-inspect.py` route. That inspector may write ignored frames, audio and
full-transcript scratch and must leave a bot-owned JSON receipt with exact
URL/hash, limits, output hashes, coverage/omissions and cleanup state. It must
make no network, provider, API or model-download calls.

Temporary inspection does not authorize full raw/OCR/transcript retention or a
claim of complete visual review. Durable output defaults to an attributed
paraphrase and short necessary excerpts with explicit sampled coverage. After
source and independent review completion, a separate cleanup may remove only
new regular files listed in the receipt/manifest whose hashes still match; it
must refuse symlinks, changed/unrelated files, broad directories and incomplete
runs. Failed or interrupted staging remains for explicit disposition.

### `--commit-staged <path>`

Copy one approved staged media file into its intended final `_media/` target.
The caller coordinates reviewed source/media notes and records partial states;
`committed: true` establishes byte custody only, not note publication, source
accuracy, retention permission or completion of the ingestion workflow. Do not
declare a source's media captured before its bytes and description note exist.

Required flags:
- `--target-path <knowledgebase/_media/...>`: the `target_path` returned by staged `--capture`.
- `--media-id <sha256>`: full lowercase SHA-256 of the approved staged bytes.

Optional flags:
- `--kb-root <path>`: defaults to `<kb-root>`.
- `--max-bytes <N>`: positive per-file bound, default 25 MiB.
- `--no-write`: validate and return a `planned` item without creating a lock,
  directory or destination. Unlike capture mode, this performs no fetch.

Output:

```json
{
  "committed": [
    {
      "staged_path": "_tools/staging/media/example/<sha256>.png",
      "local_path": "knowledgebase/_media/images/2026/05/2026-05-14-source-slug-hero-abcd1234.png",
      "target_path": "knowledgebase/_media/images/2026/05/2026-05-14-source-slug-hero-abcd1234.png",
      "media_id": "<sha256>",
      "bytes": 18393,
      "committed": true,
      "already_present": false,
      "staged_preserved": true
    }
  ],
  "failed": []
}
```

Finalize rules:
- Require an existing canonical KB root. Resolve both accepted path prefixes
  relative to that root, never by assuming the root directory's basename.
- Restrict targets to binary media extensions under `_media/images`, `videos`
  or `audio`; reject traversal, symlinks, hardlinks and special files.
- Read checked regular staging bytes within the size bound; verify the supplied
  hash before writes and recheck the snapshot under the shared nonblocking lock.
- Publish a unique same-directory temporary with atomic no-clobber linking and
  verify final bytes. Never overwrite a destination or delete the staging input.
- If the target already exists identically, return idempotent success and keep
  staging bytes available for review/retry.
- If the target already exists with a different hash, refuse with `target-exists-mismatched-hash`.
- Errors after a publication attempt include `publication_may_have_succeeded`.
  Inspect the destination and retry identical staging bytes; never assume rollback.
  Only the helper's own temporary file is cleaned up. Empty directories may remain.
- These are cooperative local-writer safeguards, not an isolation boundary
  against a malicious process running as the same user.

## Exit codes

- `0` — successful discovery/capture, staged finalization, or staged preview.
- `1` — fatal error (could not fetch source, invalid flags, etc.).
- `2` — partial success: some captures failed but at least one succeeded.

## Logging

The script does not write to `_log.md` directly. The calling agent records
captures and partial states through `kb-log-append.py`, under the media capture flow.

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

# Stage approved bytes while preparing reviewed notes
_tools/kb-media-capture.py --capture https://example.com/figure.png \
  --source-slug example-article \
  --stage-dir _tools/staging/media/example-article

# Finalize approved staged bytes; caller coordinates reviewed notes
_tools/kb-media-capture.py --commit-staged _tools/staging/media/example-article/<sha256>.png \
  --target-path knowledgebase/_media/images/2026/05/2026-05-14-example-article-figure-abcd1234.png \
  --media-id <sha256>
```
