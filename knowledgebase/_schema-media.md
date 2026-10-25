---
author: kb-bot
created: 2026-05-15
modified: 2026-10-25
type: meta
title: Media formats
---

# Media formats

Source `media` items have `id`, `kind`, `status`, `origin_url` and `license`.
Kinds are image/video/audio. States are captured/discovered/not-captured.
The full SHA-256 identifies retained bytes; a stable placeholder can describe
uncaptured media. A short filename hash is cosmetic, not the identity.

| State | Fields |
|---|---|
| captured | `path`, `captured` date, known MIME/bytes/dimensions/duration and `description_note`; canonical bytes and the description exist. |
| discovered | Origin and available metadata; `description_note` is optional, no invented local path. |
| not-captured | A descriptive `not_captured_reason`; optional description, no claim of retained bytes. |

Canonical paths use `knowledgebase/_media/<kind>/YYYY/MM/<date>-<description>-<8hash>.<ext>`.
Filename slugs are lowercase ASCII letters/digits/hyphens, bounded by the capture
tool. Full metadata retains provenance and actual licensing posture, such as
public-domain, cc-by, cc-by-sa, cc0, quoted-with-attribution, unknown or
do-not-redistribute. Unknown is not clearance; a license label is not legal proof.

## Media-description note

Use standard note metadata with `type: media-description`, source links,
`media_id`, `media_kind`, `media_status`, `origin_url` and `local_path` only for
captured bytes. Mutable descriptions hold later capture state, OCR, transcripts
and interpretation. The parent's published `media` list stays frozen.

Captured image descriptions include `## OCR` and `## Vision Description`.
Pending extraction/interpretation may say so explicitly. Captured video/audio
include `## Transcript`, with honest coverage. A placeholder is not completed
analysis. Provenance, Capture Status and Interpretation Notes distinguish
source observation from inference. Embed retained images and link other bytes.

`## OCR Raw` preserves an engine-labelled extraction attempt only when retention
is permitted, with length, UTF-8 hash and local input reference. Use a safe fence
for extracted headings/backticks. OCR may be wrong and does not prove visual
interpretation. Restricted temporary full OCR/transcripts remain private scratch.

## Temporary inspection records

Non-retained inspected media use not-captured with a reason such as temporarily
inspected; not retained. Known hash/MIME/bytes/dimensions/duration may remain,
but `path`, `local_path` and captured date are absent. Reference the inspection
receipt, sampled coverage and omissions; preserve any older frozen source status.

Tool-owned JSON receipts identify run/asset, exact URL, staged input/hash/bytes,
bounded settings, local engine/cache identifiers, derived paths/hashes and
coverage/cleanup disposition. They contain no raw media or full transcript/OCR.
The inspector/capture/cleanup tools own exact JSON schemas and exceptional
recovery fields. The [media flow](_workflows/media.md) defines when they apply.
