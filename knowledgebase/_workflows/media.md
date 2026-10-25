---
author: kb-bot
created: 2026-10-25
modified: 2026-10-25
type: meta
title: Media capture and inspection
---

# Media capture and inspection

Read the [write contract](../_rules.md), [_schema-media.md](../_schema-media.md)
and the selected tool's CLI contract only when media is relevant. Discovery does
not capture bytes. Prefer material figures, maps, charts and documentary images;
skip irrelevant covers, portraits, adverts and previews.

## Canonical capture

Use `kb-media-capture.py` with explicit root and authorized URLs. Its documented
transport, size, slug and hash checks remain authoritative. The default is a
25 MiB per-source budget and a finite caller wall deadline. Native viewing and
approved local extraction do not grant account, model-download or API authority.
Record origin, hash, capture date and actual licensing posture. A metadata label
does not establish rights to retain or redistribute restricted/private material.

Canonical bytes live in `_media/<kind>/YYYY/MM/` with descriptive stable filenames
and a short hash. Check full SHA-256 before storing another copy. Reuse existing
bytes when equal. Create/validate the mutable media-description note before a
source claims complete captured-media mappings. Embed a retained image near its
description. Later OCR, interpretation and capture state change that note, not
the frozen parent source's `media` block. Missing bytes remain not-captured.

For ordinary video discovery, record the video URL; a relevant approved poster
may be an image. Do not automatically download video/audio, resolve embedded
players or escalate renditions. Unattended workers use only their deployment's
explicit media capabilities and scope, never the temporary-inspection lane.

## Temporary inspection

The existing `--temporary-inspection` mode requires a finite explicitly approved
list of exact HTTPS image/MP4/WebM URLs and fresh private ignored staging.
It makes no redirects, proxy/cookie/login/DRM workarounds, playlist expansion,
unlisted variants or automatic retries. Bound transfer bytes and total wall time;
the socket timeout alone is insufficient. Use the exact CLI contract in
`_tools/kb-media-capture.md` and local inspection/cleanup helpers.

Hash-check the same staged files and derivatives for writer and independent
reviewer. Local extraction makes no network/provider/model-download calls.
Receipts record exact inputs, settings, hashes, coverage and omissions, not raw
media or complete transcripts. A durable note normally keeps attributed
paraphrase and necessary short excerpts. It cannot claim full visual review from
sampled frames. Non-retained inspection has no canonical media path/capture date.

Canonical retention and temporary cleanup are separate explicit decisions.
Cleanup follows completed review and source disposition, including deferred or
declined work. It removes only newly created manifest-named regular files whose
hashes still match. It refuses changed/unrelated files, symlinks, broad directories
and incomplete review. The documented hash-pinned staging-recovery and reviewed
unusable-ASR exceptions remain restricted to their exact CLI guards; they are
not general retries or permission to discard interrupted evidence.

## Transport

Use existing documented mirror rewriting for gated social hosts, with the
canonical URL in `origin` and actual transport in `mirror`. A different URL
version is not automatically equivalent. Failed mirrors leave an honest access
limitation and retry disposition, not a claim of permanent source disappearance.
