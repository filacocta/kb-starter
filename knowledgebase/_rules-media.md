---
author: kb-bot
created: 2026-05-15
modified: 2026-05-15
type: meta
---

# KB Agent Rules — Media

Rules governing media capture, enrichment, and web-host mirroring. Split out of `_rules.md` so that core ingestion skills do not load this material on startup; load this file only when a source carries (or might carry) media, or when fetching from a mirrored host.

Read `_schema-media.md` for the structural media spec; this file covers behaviour and decision-making.

---

## Rule 29 — Media Capture and Enrichment

Media discovered during ingestion is part of the source record. Capture enough provenance to make the media traceable even when the bytes are not downloaded yet.

**Source-note rule:** A source note may include a `media:` frontmatter list at initial write time. After the source note is written, do not edit it just to add OCR, transcripts, descriptions, or analysis. Source notes remain immutable under Rule 11.

**Enrichment rule:** Any later work on the media belongs in a separate `type: media-description` note at `topics/<topic>/notes/media/<sha256-or-slug>.md`. These notes are mutable kb-bot notes and may be updated with OCR, vision descriptions, transcripts, interpretation, and links to concepts.

**Storage rule:** Captured media bytes live under `knowledgebase/_media/`, grouped by kind and capture date with human-readable filenames. Keep the full SHA-256 hash in note metadata, but use only the first 8 characters in the filename. Do not include topic slugs in media paths; topic renames must not break media references.

```text
knowledgebase/_media/images/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
knowledgebase/_media/videos/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
knowledgebase/_media/audio/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
```

Example: `knowledgebase/_media/images/2026/05/2026-05-14-cordaro-forecast-success-graph-6cccb6d1.png`.

**Embed rule:** A captured image should be embedded near the top of its `media-description` note, directly below the H1, using an Obsidian image embed:

```markdown
![[knowledgebase/_media/images/YYYY/MM/<filename>.png|Human-readable caption]]
```

If the media is video or audio, link the local file prominently near the top of the note and include any poster image or transcript when available.

**Source immutability rule:** The source note's `media:` block is frozen at first write. If a source note records media as `status: discovered`, do not edit that source note later when the bytes are captured. Capture state lives in the media-description note's `media_status`.

**Deduplication rule:** Before writing media bytes, compute SHA-256 and search existing `media_id:` values across `topics/*/notes/media/*.md`. If the hash already exists, do not write a duplicate file. Reuse the existing `local_path` and update the relevant kb-bot-authored media-description note with the new source citation, or create a lightweight cross-reference note if the existing media-description note belongs to a different topic.

**Slug rule:** Filename slugs must be lowercase ASCII using only `a-z`, `0-9`, and single hyphens. Cap the descriptive slug at 60 characters. Filenames are frozen after first write; do not rename media just because a better description is found later.

**Fetch approval:** Downloading images, video, audio, thumbnails, transcripts, or metadata from the web counts as fetching external content. Rule 4 applies: list every URL to fetch, explain why, and wait for explicit user approval before fetching.

**Headless exception:** When an agent is running in a documented headless mode (e.g. `/kb-auto`, scheduled batch jobs), it may auto-capture media without per-URL prompting under all of these conditions: (a) the agent's skill explicitly declares headless auto-capture is in effect, (b) the license posture is recorded as `quoted-with-attribution`, (c) total captured bytes per source are bounded (default cap: 25 MB per source), and (d) every captured URL is still listed in `_log.md` and in the relevant media-description note. Interactive modes (`/kb-ingest`, `/kb-queue`) always require explicit per-source approval.

**Video handling (v1):** For HTML5 `<video>` elements, capture the `poster` attribute as a normal image (kind `image`, with a `from_video` field pointing at the video URL in the media-description note). Do not download `.mp4`/`.webm` bytes. Embedded video iframes (YouTube, Vimeo, etc.) are recorded as discovered URLs only; their thumbnails are not auto-fetched in v1. Audio is recorded as discovered URLs only; bytes are not downloaded in v1.

**Capture process:**
1. Record media listed by a source as `status: discovered` if bytes have not been fetched.
2. When fetching is approved, download to a temporary file, compute SHA-256, record MIME type and byte size, and move the file under `_media/` using the human-readable filename convention.
3. Create or update the matching media-description note with provenance and capture status.
4. Append the capture to `_log.md`: origin URL → media id/hash → local path.
5. If fetching fails or is blocked, set the media-description note's `media_status: not-captured` and record the failure there. Do not lose the URL.

**Lint expectations:** KB linting should flag orphan media files, missing embed targets, duplicate `media_id` values, and captured media-description notes whose `local_path` does not exist.

**Copyright and provenance posture:** Always store `origin_url`, capture date when available, and a license posture. Use `quoted-with-attribution` as the default for small web/social-media media captured for private research. Use `unknown` when unsure. Use `do-not-redistribute` when media is paywalled, explicitly restricted, or should not be stored locally; in that case keep the URL and notes but do not save the bytes.

---

## Rule 30 — Web Host Mirroring

Some hosts gate anonymous requests behind paywalls, auth walls, or JS-only rendering. For those, fetch via a documented public mirror that proxies the underlying content. The canonical URL stays in the source note's `origin:` field as the user-approved identifier; the mirror used to actually retrieve the content goes in a separate `mirror:` field for provenance.

**Known mirrors (2026-05-14):**

| Source host | Mirror (HTML) | Mirror (structured) | Notes |
|---|---|---|---|
| `x.com`, `twitter.com` | `fxtwitter.com` (also `vxtwitter.com`, `fixupx.com`) | `api.fxtwitter.com/2/status/<id>`, `api.fxtwitter.com/2/thread/<id>` | Bypasses HTTP 402 on anonymous fetch. Video-only posts may 302-redirect back to x.com — treat as not-captured. |
| `nitter.*` | (already a mirror) | — | Some instances rate-limit; rotate if blocked. |

**Required behaviour for all agents and tools:**

1. **Automatic rewrite at fetch time.** When the URL host is on the rewrite list above, rewrite to the mirror before the HTTP request. Do not ask the user about the rewrite — they approved the canonical URL; the mirror is a transport detail.
2. **Record both URLs.** In the resulting source note frontmatter:
   - `origin: "<canonical URL the user approved>"`
   - `mirror: "<actual URL fetched>"`
   - If a structured API endpoint was used (e.g. `api.fxtwitter.com/2/thread/...`), prefer it for the mirror value because it returns reliable media URLs.
3. **Media on these sources.** Discovered media URLs typically resolve to CDN hosts (e.g. `pbs.twimg.com`, `video.twimg.com`) and fetch fine without further rewriting. Capture as normal.
4. **Failure mode.** If all mirrors fail, log the canonical URL in `reading-list.md` with `- [!]` and the reason. Do not silently fall back to fetching the canonical host — that just gets you the same 402.
5. **Source-note text.** When the mirror returns text content (tweet body, article), record it under a `## Mirrored Text` heading so future readers know they are reading mirror output, not the canonical render.

**Adding mirrors.** If you discover a new host that needs mirroring, update this rule's table with the source host, the mirror, and the date discovered. Mirrors are operational knowledge that must outlive any single session.
