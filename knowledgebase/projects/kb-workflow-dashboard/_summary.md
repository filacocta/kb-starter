---
author: kb-bot
created: 2026-06-16
modified: 2026-06-17
type: summary
topic: kb-workflow-dashboard
title: KB Workflow Dashboard
description: Local read-only web dashboard for seeing KB workflow state, surfaced holdups, and eventually safe command execution from one place.
status: scaffold
tags: ["kb", "workflow", "dashboard", "webapp", "tooling", "obsidian"]
sources: []
related: ["[[knowledgebase/projects/kb-feed/_summary]]", "[[knowledgebase/ideas/_backlog]]"]
---

# KB Workflow Dashboard

## LLM Orientation

KB Workflow Dashboard is optional KB infrastructure. The starter implementation lives at `projects/kb-workflow-dashboard` in this repository. It is a dependency-free Python stdlib server plus static HTML/CSS/JS that binds to `127.0.0.1`, validates the configured KB root, reads the KB live on each `/api/status` request, and serves a local dashboard UI.

The MVP is deliberately read-only. It surfaces checkpoint status, global inboxes, ideas backlog, topic reading-list counts, generated dive-queue counts, project todo counts, kb-feed SQLite outbox/capture state in read-only mode, Needs Attention triage cards, and recent KB log entries. `/api/status` returns summaries and short previews; `/api/queue?path=<relative-kb-path>` returns full checkbox item details for allowlisted queue paths only. It parses `[?]` review/testing rows separately from `[!]` blocked rows, accepts uppercase `[X]` as done, and shows newest parsed log entries first. It does not edit markdown or run commands.

The current starter-safe path forward is captured in [[knowledgebase/projects/kb-workflow-dashboard/notes/2026-06-17-path-forward]]. Future live actions should start with a named, audited allowlist: lint, generated dive-queue regeneration, locked checkoff, path helpers, and possibly server start/stop after port ownership is shown. Agent/skill execution should begin as copyable handoff prompts and only later become whitelisted CLI launches with checkpoint enforcement, visible prompt/transcript, cancel behavior, dirty-state checks, and post-run lint. External skill artifacts should be reviewed through a scratch checkout, normalized candidate package, validator pass, KB synthesis comparison, and explicit install/adapt/extract/ignore decision before any installation or starter inclusion.

The starter-safe `book-to-skill` meta-skill adds a future dashboard lane for long-form source work: books, reports, article-length PDFs, source clusters, and external skill artifacts should move through cataloging, triage, extraction, synthesis, candidate-skill review, eval fixtures, and promotion/ignore decisions. Domain skills should not be installed just because a book exists.

## User Research Direction

Use the dashboard as a situational-awareness pane first. Add write actions only after the read-only view proves useful. Any future mutation must call shared KB tools such as `_tools/kb-checkoff.py`; the dashboard should never edit queue files directly.

## Open Questions

- Which status panels are most useful in daily operation?
- Should the first command runner be added as a separate API with a strict whitelist?
- Should this eventually become an Obsidian plugin, or stay as a local webapp?
- Should source-cluster-to-skill workflows become a dashboard lane after the manual workflow proves useful?
- Should long-form source custody use a `books/`, `library/`, or another cataloged path outside topic folders?
- What minimum candidate-skill registry fields should the dashboard expose before install/run actions exist?
