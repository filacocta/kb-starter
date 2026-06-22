---
author: kb-bot
created: 2026-06-16
modified: 2026-06-16
type: todos
topic: kb-feed
title: kb-feed — Task Board
description: Setup checklist and future work for the Telegram-to-KB capture bot.
---

# kb-feed — Task Board

## 🗂️ Backlog

### Setup

- [ ] Create Telegram bot and private supergroup.
- [ ] Configure `~/.config/kb-feed/credentials.env`.
- [ ] Configure `~/.config/kb-feed/kb-feed.toml`.
- [ ] Initialize SQLite state with `kb-feed state init`.
- [ ] Map Telegram topics to KB topic slugs.
- [ ] Run fixture dry-run and flush tests before touching the live KB.
- [ ] Enable systemd user services/timers only after manual live verification.

### Operations

- [ ] Decide whether weekly digest/review is enabled.
- [ ] Decide whether any topic lane should use trusted auto-approval.
- [ ] Review failed outbox/capture rows regularly.

## 🔄 In Progress

*(none)*

## 🧪 Testing / Review

*(none)*

## ✅ Done

*(none)*
