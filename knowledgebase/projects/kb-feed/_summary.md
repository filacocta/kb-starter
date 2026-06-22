---
author: kb-bot
created: 2026-06-16
modified: 2026-06-16
type: summary
topic: kb-feed
title: kb-feed
description: Telegram supergroup pipeline for routing mobile-shared URLs and lightweight notes into KB queues.
status: scaffold
tags: ["telegram", "kb", "automation", "python", "reading-list", "ingestion"]
sources: []
related: ["[[knowledgebase/_rules]]", "[[knowledgebase/_dive-queue]]"]
---

# kb-feed

## LLM Orientation

kb-feed is optional KB infrastructure. It watches a private Telegram supergroup, validates messages from configured users, maps Telegram topic threads to KB topic slugs, and writes URLs or lightweight notes into the appropriate KB queue files.

The starter implementation lives at `projects/kb-feed` in this repository. Runtime secrets and state are intentionally outside the repo:

- `~/.config/kb-feed/credentials.env` for secrets
- `~/.config/kb-feed/kb-feed.toml` for local configuration
- `~/.local/state/kb-feed/state.sqlite` for runtime state

The KB remains the source of truth. kb-feed writes queue files only through guarded append/checkoff paths and never fetches URLs or ingests sources itself.

## User Research Direction

Set up kb-feed only after the base KB works. Keep the first live run small: map one test topic, send one URL, flush to a non-live fixture first, then run a guarded live flush. Review the resulting markdown diff before enabling timers.

## Open Questions

- Which Telegram topics should map to which KB topics?
- Should any lane be trusted for `## Auto-approved`, or should all captured URLs stay interactive at first?
- Should weekly digest/review use a local agent CLI, a hosted LLM API, or be disabled?
