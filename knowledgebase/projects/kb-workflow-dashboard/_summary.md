---
author: kb-bot
created: 2026-06-16
modified: 2026-06-16
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

KB Workflow Dashboard is optional KB infrastructure. The starter implementation lives at `projects/kb-workflow-dashboard` in this repository. It is a dependency-free Python stdlib server plus static HTML/CSS/JS that binds to `127.0.0.1`, reads the KB live on each `/api/status` request, and serves a local dashboard UI.

The MVP is deliberately read-only. It surfaces checkpoint status, global inboxes, ideas backlog, topic reading-list counts, generated dive-queue counts, project todo counts, kb-feed SQLite outbox/capture state in read-only mode, and recent KB log entries. It does not edit markdown or run commands.

## User Research Direction

Use the dashboard as a situational-awareness pane first. Add write actions only after the read-only view proves useful. Any future mutation must call shared KB tools such as `_tools/kb-checkoff.py`; the dashboard should never edit queue files directly.

## Open Questions

- Which status panels are most useful in daily operation?
- Should the first command runner be added as a separate API with a strict whitelist?
- Should this eventually become an Obsidian plugin, or stay as a local webapp?
