---
author: kb-bot
created: 2026-05-18
modified: 2026-09-17
type: meta
title: Explicit OCR enrichment
description: Configured OCR extraction with guarded media-note updates.
---

# Explicit OCR enrichment

Use `--ocr-only` with an explicitly selected available `--ocr-engine`.
Preview with `--dry-run` first: it must not invoke an engine or write notes.
See executable `--help` for supported engines and configuration. Hosted extraction
requires an approved account, data-sharing scope and budget; availability of a
credential does not grant those permissions. Do not switch engines/accounts
automatically after failure.

The tool updates only owned mutable media-description notes backed by matching
local bytes. It snapshots under the shared write lock, extracts outside it, then
rechecks identities and note bytes before committing. Concurrent changes stop
the write even when forcing re-extraction. Source media metadata stays frozen.

OCR output and visual interpretation are separate evidence channels. Retain
engine-labelled raw/cleaned provenance only where rights allow; raw OCR can be
wrong. Use an explicitly configured image-capable reviewer for visual meaning.
The old direct-model `--vision-only`/`--both` route is unsupported. No particular
reasoning model is required by the shared workflow.
