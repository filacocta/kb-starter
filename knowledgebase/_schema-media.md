---
author: kb-bot
created: 2026-05-15
modified: 2026-05-15
type: meta
---

# KB Schema — Media

Structural specification for media frontmatter on source notes and for `type: media-description` notes. Split out of `_schema.md` so that core ingestion skills do not load this material on startup; load this file only when a source carries (or might carry) media.

Read `_rules-media.md` for the behavioural rules (Rule 29 capture/enrichment, Rule 30 host mirroring).

---

## Media Schema

Source notes may include a `media:` frontmatter list. This list records media discovered during source capture. The source note remains immutable after initial write; later OCR, vision descriptions, transcripts, and interpretation live in `type: media-description` notes under `topics/<topic>/notes/media/`.

```yaml
media:
  - id: "<sha256-of-bytes-or-stable-placeholder>"
    kind: image | video | audio
    status: captured | discovered | not-captured
    path: "knowledgebase/_media/images/YYYY/MM/<YYYY-MM-DD>-<description>-<short-hash>.<ext>"
    origin_url: "https://example.com/media.png"
    captured: <ISO-8601 date or empty>
    mime: "image/png"
    bytes: 41234
    width: 493
    height: 299
    duration_seconds: 12.4
    license: public-domain | cc-by | cc-by-sa | cc0 | quoted-with-attribution | unknown | do-not-redistribute
    description_note: "[[knowledgebase/topics/<topic>/notes/media/<sha256-or-slug>]]"
```

**Field rules:**
- `id`, `kind`, `status`, `origin_url`, and `license` are required for every media item.
- `description_note` is required when `status: captured` (a media-description note exists for it). For `status: discovered` and `status: not-captured`, `description_note` may be `null` or omitted — no description note has been written.
- `path`, `captured`, `mime`, `bytes`, `width`, `height`, and `duration_seconds` are filled only when known (typically populated at capture time).
- Use `status: discovered` when the source references media but bytes have not yet been fetched (no decision made yet).
- Use `status: not-captured` when a fetch was attempted but blocked, failed, declined by user, or intentionally skipped as contextually-irrelevant per the contextual relevance filter. Record a `not_captured_reason` field with a short explanation.
- Use `status: captured` only after bytes are stored under `_media/` and a corresponding `media-description` note has been written.
- Media paths are vault-relative and must begin with `knowledgebase/_media/`.
- Media filenames should be human-readable: `<YYYY-MM-DD>-<source-or-subject>-<description>-<8-char-hash>.<ext>`.
- Store topic in note metadata, not in the media file path. Topic renames must not break media paths.
- Keep the full SHA-256 in `id` for integrity and deduplication; do not make the full hash the visible filename unless there is no human-readable context.
- The 8-character hash suffix is cosmetic; the full `id` is authoritative.

Media-description notes use this frontmatter:

```yaml
---
author: kb-bot
created: <ISO-8601 date>
modified: <ISO-8601 date>
type: media-description
topic: <topic-slug>
title: <human-readable title>
description: <one sentence describing the media item and source context>
status: draft | review | final
tags: []
sources: ["[[knowledgebase/topics/<topic>/sources/<source-note>]]"]
related: []
media_id: "<sha256-or-stable-placeholder>"
media_kind: image | video | audio
media_status: captured | discovered | not-captured
origin_url: "https://example.com/media.png"
local_path: "knowledgebase/_media/images/YYYY/MM/<YYYY-MM-DD>-<description>-<short-hash>.<ext>"
---
```

Recommended sections for media-description notes. **For `media_status: captured` image notes, the `## OCR` and `## Vision Description` sections are REQUIRED** — always write them, using `*Pending.*` as the placeholder when not yet enriched, so `kb-ocr-enrich.py` (which only fills sections that already exist) can populate them later. `kb-lint` reports a captured image note missing `## OCR` as `media-missing-ocr`. (`## Transcript` is required for video/audio.)

```markdown
# <Title>

![[knowledgebase/_media/images/YYYY/MM/<YYYY-MM-DD>-<description>-<short-hash>.<ext>|Human-readable caption]]

## Provenance

## Capture Status

## OCR

## Vision Description

## Transcript

## Interpretation Notes
```
