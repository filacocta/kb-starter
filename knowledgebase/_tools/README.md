---
author: kb-bot
created: 2026-06-21
modified: 2026-06-21
type: meta
title: KB Tools — Workflow & Endpoint Reference
description: Authoritative index of the _tools/ scripts (the KB's programmatic endpoints), the queue write contract, the source-of-truth vs generated files, and the canonical capture→ingest→synthesise workflow.
---

# KB Tools — Workflow & Endpoint Reference

The `_tools/` scripts are the KB's **programmatic endpoints** — the only sanctioned
way for any agent (LLM or script) to mutate queue/markdown state. This file is the
single place to learn *which tool does what* and *which files are authoritative vs
generated*, so no agent has to reverse-engineer the contract. Read `_schema.md`
(structure) and `_rules.md` (behaviour) for the full specification; this is the
operational index that points into them.

## The write-lock contract (non-negotiable)

Every mutation of a queue or reading-list file goes through a tool that holds the
shared exclusive lock `<kb-root>/.kb-write.lock`, re-reads the target inside the
lock, and writes atomically (temp file + `os.replace`). This is how interactive
sessions, `/kb-auto`, and kb-feed mutate the same files concurrently without races.
**Never hand-edit queue markdown** to add/move/check items — call the matching tool.

## Source of truth vs generated

| File | Status | Written by |
|---|---|---|
| `topics/<t>/reading-list.md` (the section ladder) | **source of truth** | kb-capture-url (add), kb-promote (move), kb-checkoff (mark) |
| `topics/<t>/research-ideas.md` | source of truth | agents (prose rabbit holes) |
| `readinglist/Links.md` (global URL inbox) | source of truth | kb-capture-url, kb-feed |
| `readinglist/topics.md` (broad-interest inbox) | source of truth | kb-feed, agents |
| `ideas/_backlog.md` | source of truth | kb-checkoff `--route-ideas-backlog`, agents |
| `_dive-queue.md` | **GENERATED — read-only** | kb-derive-dive-queue (never hand-edit) |

`_dive-queue.md` is **not deprecated** — what was deprecated is treating it as a
*writable* queue. It is now strictly a generated cross-topic dashboard derived from
every topic's `## Auto-approved` unchecked items plus `research-ideas.md`. The
queue-mutating tools best-effort regenerate it after a write (so promoting an item
into/out of `## Auto-approved` keeps the dashboard correct), and it is regenerated
nightly. Agents and scripts must not append to it (Rule 13).

## The section ladder (per-topic reading list)

Five canonical level-2 sections, in order — enforced by `kb-lint.py`:

`## Pending → ## Approved → ## Auto-approved → ## Processed → ## Failed — Retry`

Items are distinguished by **section**, not by checkbox mark (Pending and Approved
items are both `- [ ]`). So advancing an item is a **move between sections**, and
`## Auto-approved` is the only headless-ingestion lane. See `_schema.md` →
"Topic Reading Lists" for the full table and the Auto-approved gate.

## The canonical workflow

```
CAPTURE            TRIAGE              LADDER (per topic)              INGEST            SYNTHESISE
Links.md   ──▶  assign to a topic ──▶ Pending ─▶ Approved ─▶ Auto-approved ─▶ source notes ─▶ wiki notes
topics.md       (research-ideas.md     (kb-promote moves items     (/kb-queue       (/kb-synthesise)
Telegram         for prose ideas)       up the ladder)              interactive,
(kb-feed)                                                           /kb-auto headless
                                                                    → kb-checkoff [x]
                                                                    → moves to Processed)
```

## Endpoints — queue write verbs (the contract core)

| Tool | Verb | What it does | Use when |
|---|---|---|---|
| `kb-capture-url.py` | **ADD** | Append a *new* URL under a topic's `## Pending` (`--topic`) or to `readinglist/Links.md` (global inbox), with agent/session provenance. Lock-safe. Backs the WebFetch capture hook. | Capturing a freshly-seen URL into the pipeline. |
| `kb-promote.py` | **MOVE** | Move an existing `- [ ]` line between canonical ladder sections (`--from`/`--to`, matched by `--url` substring; `###` sub-groups roll up to their `##` parent). Lock-safe, atomic, regenerates the dive-queue. `--dry-run` previews; `--json` for programmatic callers. | Advancing an item Pending→Approved→Auto-approved (the only contract-safe section mover). |
| `kb-checkoff.py` | **MARK** | Flip an existing line's checkbox in place (`[ ]`→`[x]/[!]/[~]/[?]`) matched by `--url`. `--route-ideas-backlog` also appends the original to `ideas/_backlog.md`. Regenerates the dive-queue. | Marking ingested (`[x]`) or permanently failed (`[!]`) per Rule 18. |
| `kb-derive-dive-queue.py` | **GENERATE** | Rebuild `_dive-queue.md` from Auto-approved + research-ideas. Dry-run by default; `--write` to commit. | Rarely called by hand — the mark/move tools and the nightly job invoke it. |

## Endpoints — pre-fetch & dedup (read-only)

| Tool | What it does | Use when |
|---|---|---|
| `kb-seen.py` | Ingestion-level dedup: is this URL already ingested? (`--check`). | **Before any fetch** — dedup is mandatory (CLAUDE.md). |
| `kb-prep-item.py` | Read-only pre-fetch prep: dedup + transport mirror rewrite + media discovery. Writes nothing. | Centralised pre-fetch step for interactive and headless ingest. |

## Endpoints — fetch & media capture

| Tool | What it does |
|---|---|
| `kb-media-capture.py` | Discover and capture media on a source page (CLI contract in `kb-media-capture.md`). All ingest skills call it; none re-implement scraping. |
| `kb-headless-capture.py` | Headless-Chromium fallback for JS-rendered / Cloudflare-gated pages and element screenshots. |
| `kb-tweet-fetch.py` | Fetch structured tweet/thread data via fxtwitter for ingestion. |
| `kb-video-analyze.py` | Analyze a video URL into a structured transcript/ingestion artifact. |
| `kb-ocr-enrich.py` | OCR + vision-description enrichment for `type: media-description` notes (uses the current `google-genai` SDK). |
| `kb-link-media-backlinks.py` | Suggest (and optionally apply) See Also backlinks from media to concept/synthesis notes. |

## Endpoints — diagnostics (read-only)

| Tool | What it does |
|---|---|
| `kb-lint.py` | Structural lint: canonical-ladder enforcement, broken wikilinks, frontmatter, stale sources, media integrity. `--all` for the whole KB. |
| `kb-obsidian-cli.py` | Optional read-only bridge to the Obsidian CLI for diagnostics/search (requires the desktop app running). |

## What does NOT exist (to prevent re-confusion)

- There is **no generic command runner or arbitrary section editor** — mutations are
  limited to the verbs above (add / move / mark / generate).
- `_dive-queue.md` has **no writer** other than `kb-derive-dive-queue.py`.
- Tests live in `_tools/tests/` (pytest, `tmp_path` fixtures, importlib-loaded
  hyphenated scripts). Run with a pytest-equipped interpreter, e.g.
  `~/Vault/projects/kb-feed/.venv/bin/python -m pytest _tools/tests/`.
