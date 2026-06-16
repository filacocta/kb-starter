---
author: kb-bot
created: 2026-05-14
modified: 2026-05-15
type: meta
title: KB Media Store
description: Policy and path conventions for captured media assets in the knowledge base.
---

# KB Media Store

This folder stores media captured from ingested sources. Media is preserved for private research provenance, not for public redistribution.

## Layout

Captured bytes are stored by kind and capture date with human-readable filenames:

```text
images/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
videos/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
audio/YYYY/MM/<YYYY-MM-DD>-<source-or-subject>-<description>-<short-hash>.<ext>
```

Example:

```text
images/2026/05/2026-05-14-cordaro-forecast-success-graph-6cccb6d1.png
```

The full SHA-256 hash remains the stable media id in the media-description note. The filename keeps a short 8-character hash suffix so duplicates can still be spotted without making the file browser unreadable.

Topic slugs do not belong in media paths. Topics can be renamed or reorganised; media file paths should remain stable.

## Provenance

Every media item should be traceable from three places:

1. The source note's `media:` frontmatter entry
2. The topic media-description note in `topics/<topic>/notes/media/`
3. The activity log entry in `knowledgebase/_log.md`

At minimum, preserve:

- Original media URL
- Source note that referenced it
- Capture date, if bytes were fetched
- SHA-256 hash, if bytes were fetched
- Local path as plain text, if bytes were fetched
- MIME type and byte size, if known
- License posture

## License Posture

Use the most cautious applicable value:

- `public-domain`
- `cc-by`
- `cc-by-sa`
- `cc0`
- `quoted-with-attribution` — default for small web or social-media media kept as private research evidence
- `unknown`
- `do-not-redistribute` — keep provenance and notes, but do not store local bytes

This is a personal research convention, not legal advice. Do not publish or re-host files from this folder without separately checking rights.

## Enrichment

Do not add OCR, visual descriptions, transcripts, or interpretation to source notes after initial capture. Put enrichment in `type: media-description` notes under:

```text
topics/<topic>/notes/media/
```

This keeps immutable source records separate from later analysis.

For captured images, embed the local image directly below the H1 in the media-description note:

```markdown
![[knowledgebase/_media/images/YYYY/MM/<filename>.png|Human-readable caption]]
```

In provenance sections, write the local path as code text rather than a second wikilink. The embed is for rendering; provenance is for audit.

## Source-Note Embeds

When a source note captures media, the embeds live in a dedicated `## Captured Media` section near the top of the source note (after `## Capture Details` if present, before `## Summary`). One Obsidian embed per captured image with a human-readable caption. A trailing wikilink to the media-description note belongs in the same section so a reader can jump to OCR / vision description / interpretation.

```markdown
## Captured Media

![[knowledgebase/_media/images/YYYY/MM/<filename>.png|Human-readable caption]]

Description: [[knowledgebase/topics/<topic>/notes/media/<slug>]]
```

## Video and Audio (v1)

- HTML5 `<video poster="...">`: capture the poster as a normal image. Record `from_video: <video-url>` in the media-description note's frontmatter so the relationship is preserved.
- Embedded video iframes (YouTube, Vimeo, etc.): record the iframe `src` as a discovered URL. Do not fetch thumbnails automatically in v1.
- Audio: record URLs as discovered. Do not download bytes in v1.

## Capture Tool

The reusable scraper lives at `knowledgebase/_tools/kb-media-capture.py`. Skills call it in two modes:

- `--discover <source-url>` prints JSON of discovered media (no bytes fetched).
- `--capture <url> [...]` downloads approved URLs, hashes, dedups, writes to the date-based path, and prints JSON metadata ready for note frontmatter.

See `_tools/kb-media-capture.md` for the full CLI contract.
