---
author: kb-bot
created: 2026-05-18
modified: 2026-05-18
type: meta
title: kb-ocr-enrich CLI Contract
description: CLI contract for OCR + Vision Description enrichment of KB media-description notes.
---

# kb-ocr-enrich.py — CLI Contract

A standalone Python script that fills in the `## OCR` and `## Vision Description` sections of media-description notes (`topics/*/notes/media/*.md`) whose content begins with `*Pending.*` markers. Idempotent: re-runs skip already-enriched sections.

Location: `knowledgebase/_tools/kb-ocr-enrich.py`
Executable: `chmod +x` so `./kb-ocr-enrich.py ...` works directly.
Bootstrapping: PEP 723 inline metadata; `uv run` auto-installs `google-genai>=0.3` on first invoke.

## External dependencies

- **Tesseract OCR** — `sudo apt install tesseract-ocr`. Required for `--ocr-only` and `--both`.
- **GEMINI_API_KEY** — environment variable. Required for `--vision-only` and `--both`. Get a key at https://aistudio.google.com/apikey.

## Modes

The script accepts exactly one of three stage flags (default behaviour with none = both stages):

- `--ocr-only` — Tesseract on the local image, writes the `## OCR` section.
- `--vision-only` — Gemini 2.5 Flash on the local image, writes the `## Vision Description` section.
- `--both` — Run both stages on each note.

Mutually exclusive: passing both `--ocr-only` and `--vision-only` errors out.

## Scope flags

- `--topic <slug>` — process every media-description note in `topics/<slug>/notes/media/`.
- `--note <path>` — process a single note (relative to `--kb-root` or absolute).
- `--kb-root <path>` — overrides the default `~/Vault/knowledgebase`.
- `--limit N` — stop after N notes (0 = unlimited).
- `--dry-run` — run the pipeline (incl. API calls) but do not write files.

## Usage examples

```bash
# Pilot a single topic with OCR only, capped at 3 notes
./kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --limit 3

# Full topic, vision only (Gemini API key required)
./kb-ocr-enrich.py --topic earthquake-prediction --vision-only

# One-off enrichment of a single note, both stages
./kb-ocr-enrich.py --note topics/earthquake-prediction/notes/media/foo.md --both

# Inspect what would change without writing
./kb-ocr-enrich.py --topic earthquake-prediction --dry-run

# Run from another agent (Claude/Codex) and avoid pulling JSON back into context
./kb-ocr-enrich.py --topic earthquake-prediction --vision-only > /tmp/vision.log 2>&1
```

## Rate-limit handling (Gemini)

Free-tier quotas vary substantially by account tier — the documented numbers are aspirational. Observed on the configured account as of 2026-05-18:

- `gemini-2.5-flash`: ~20 requests/day
- `gemini-2.5-flash-lite`: ~20 requests/day (separate pool — usable as fallback when flash is exhausted)
- `gemini-2.0-flash`: 0 requests/day (not provisioned on this tier)
- `gemini-2.5-pro`: tight, ~5 requests/day in practice

Two constants near the top of the script tune retry behaviour:

- `GEMINI_RATE_LIMIT_SLEEP_SECONDS = 20` — fixed inter-request sleep on successful calls.
- `GEMINI_429_BACKOFF_MARGIN_SECONDS = 3` — extra margin added to any retry-after delay parsed from a 429 error before a single retry.

If a single retry still fails, the note is recorded as `error: ...` for that stage and the script moves on. Daily-quota exhaustion is not retried within a run — re-run after UTC midnight, or swap `MODEL_NAME` to a different model whose quota pool is still fresh.

## Model selection

Active model lives in `MODEL_NAME` near the top of the script. Multiple commented alternatives are listed for quick swap when the active model hits its daily quota — uncomment one, comment out the other, re-run.

Default: `gemini-2.5-flash` — best quality/quota balance.

Alternatives:

- `gemini-2.5-flash-lite` — lower description quality, separate quota pool. Effective fallback when 2.5-flash is exhausted.
- `gemini-2.5-pro` — better quality on complex figures, very tight free-tier quota. Reserve for one-offs.
- `gemini-2.0-flash` — currently 0 RPD on this account tier. Listed as commented-out reminder.

Free-tier quotas shift over time and per account; verify against the dashboard at https://ai.dev/rate-limit before assuming.

## Idempotency and authorship guard

- Sections whose content does not begin with `*Pending.*` (case-insensitive) are skipped.
- Notes lacking `author: kb-bot` frontmatter are refused (KB Rule 1).
- A legacy combined header `## OCR / Vision Description` is normalized into two separate sections before processing.
- Only `topics/*/notes/media/*.md` files are touched. Source notes (Rule 11) and concept/entity notes are never modified by this script.

## When to run it

- After `/kb-ingest` or `/kb-queue` has captured new media (Pending sections written by the ingestion path).
- After a previous run was interrupted by quota exhaustion.
- After a `/kb-lint` pass surfaces media-description notes still showing Pending in their summary table.

## Token-conscious invocation

When invoked from an interactive agent session (Claude or Codex), the JSON progress output can be substantial. To avoid loading it into the agent's context:

1. Redirect stdout/stderr to a file: `... > /tmp/enrich.log 2>&1`.
2. After completion, read only the last ~20 lines of the log to confirm success.
3. The actual enrichment lives in the media-description notes themselves — verify by sampling one or two via `/kb-lint`, not by reading the script output.

## Exit codes

- `0` — completed (with or without per-note errors recorded in the JSON output).
- Non-zero — fatal config error (missing API key with `--vision-only`, missing Tesseract with `--ocr-only`, malformed args, etc.).
