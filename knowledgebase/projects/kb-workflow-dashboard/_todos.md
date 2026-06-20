---
author: kb-bot
created: 2026-06-16
modified: 2026-06-17
type: todos
topic: kb-workflow-dashboard
title: KB Workflow Dashboard — Task Board
description: Setup checklist and future work for the local KB workflow dashboard.
---

# KB Workflow Dashboard — Task Board

## 🗂️ Backlog

### Setup

- [ ] Run `python3 projects/kb-workflow-dashboard/server.py`.
- [ ] Open `http://127.0.0.1:8787`.
- [ ] Verify `/api/status` can read the intended KB root.
- [ ] Confirm the displayed queue counts match the markdown files.
- [x] Keep `/api/status` summary-only and expose full queue details only through allowlisted `/api/queue?path=...` requests (2026-06-17).
- [x] Preserve original markdown line numbers in queue item details after frontmatter/comment skipping (2026-06-17).
- [x] Mark ideas backlog as a parking lot and `_dive-queue.md` as generated/read-only in the API/UI (2026-06-17).
- [x] Add KB-root validation, loopback host guard, no-store JSON headers, and keyboard-accessible queue details (2026-06-17).
- [x] Parse `[?]` review/testing rows and uppercase `[X]` done rows in queue/todo counts (2026-06-17).
- [x] Show project review counts separately from blocked counts and include review rows in Needs Attention warnings (2026-06-17).
- [x] Make Recent Log prefer newest parsed log entries and keep mobile body overflow contained around wide tables (2026-06-17).
- [x] Add sanitized getting-started pages covering the dashboard workflow, safe operations, and source-cluster-to-skill promotion (2026-06-17).
- [x] Add a starter-safe path-forward note for future command-runner and skill handoff work (2026-06-17).

### Future Work

- [ ] Add browser screenshot/interaction tests.
- [ ] Add a whitelisted command-runner only after read-only usage is stable: lint, generated dive-queue regeneration, locked checkoff, path helpers, and possibly server start/stop.
- [ ] Add confirmation, execution logging, and post-action verification for every future write action.
- [ ] Route all future write actions through shared `_tools/` commands.
- [ ] Design skill/agent handoff support as copyable prompts first; whitelisted CLI launch requires checkpoint, transcript/log, cancel behavior, dirty-state checks, and post-run lint.
- [ ] Decide where command-runner logs belong.
- [ ] Decide whether the dashboard should stay pinned to one KB root or support multiple configured roots.
- [ ] Add a source-cluster-to-skill lane only after the manual workflow is tested.
- [x] Define an external skill-artifact review flow before installing cloneable Gists/repos as skills: scratch checkout, normalized package copy, validator pass, KB synthesis comparison, provenance/scope/test gap review, and explicit install/adapt/extract/ignore decision (2026-06-17).
- [ ] Add dashboard support for external skill-artifact review state once safe command-runner design is settled.
- [ ] Add long-form source library/catalog visibility: raw custody path, catalog state, candidate topics/projects, triage status, extraction status, and linked topic notes.
- [ ] Add candidate-skill registry visibility: source cluster, candidate package path, promotion-gate result, eval fixture status, validation decision, install status, and starter-safety.
- [ ] Add first book-to-skill test tracking: cataloged, triaged, extracted, synthesized, candidate/no-candidate decision, and follow-up sources.
- [ ] Decide the long-form source custody path: `books/`, `library/`, or another cataloged location outside topic folders.
- [ ] Decide whether candidate-skill lifecycle state belongs in KB notes only, generated JSON, skill package metadata, or a combination.
- [ ] Consider Obsidian plugin integration later.

## 🔄 In Progress

- [x] Low-risk dashboard status fixes verified by compile/static checks, parser/log assertions, localhost API smoke, and headless Chrome CDP in the live app copy (2026-06-17).

## 🧪 Testing / Review

*(none)*

## ✅ Done

*(none)*
