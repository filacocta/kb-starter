---
author: kb-bot
created: 2026-06-16
modified: 2026-06-16
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

### Future Work

- [ ] Add browser screenshot/interaction tests.
- [ ] Add a whitelisted command-runner only after read-only usage is stable.
- [ ] Route all future write actions through shared `_tools/` commands.
- [ ] Consider Obsidian plugin integration later.

## 🔄 In Progress

*(none)*

## 🧪 Testing / Review

*(none)*

## ✅ Done

*(none)*
