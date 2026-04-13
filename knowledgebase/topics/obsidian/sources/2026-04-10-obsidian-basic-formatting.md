---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: source
topic: obsidian
title: "Basic Formatting Syntax — Obsidian Help"
description: "Obsidian's basic formatting is standard Markdown with a few additions."
tags: ["obsidian", "markdown", "formatting", "syntax", "documentation"]
sources: []
related: []
origin: "https://obsidian.md/help/Editing+and+formatting/Basic+formatting+syntax"
---

# Basic Formatting Syntax — Obsidian Help

**Origin:** [Basic formatting syntax — Obsidian Help](https://obsidian.md/help/Editing+and+formatting/Basic+formatting+syntax)
**Ingested:** 2026-04-10
**Type:** article

## Summary

Obsidian's basic formatting is standard Markdown with a few additions. Paragraphs are separated by blank lines; a single Enter creates line continuation. The full set of inline styles is supported: bold (`**text**` or `__text__`), italic (`*text*` or `_text_`), strikethrough (`~~text~~`), highlight (`==text==`), and bold-italic (`***text***`). Highlight (`==text==`) is an Obsidian extension not present in standard CommonMark.

Links follow the dual-format pattern: internal links use `[[Note name]]` (wikilink syntax), external links use `[text](URL)`. Code uses backticks inline and triple-backtick fenced blocks with optional language identifiers. Block quotes use `>`. Horizontal rules use `***`, `---`, or `___`. Footnotes use `[^reference]` syntax. Comments (hidden from rendered view) use `%%text%%`.

## Key Points

- `==highlight==` is Obsidian-specific — not in standard Markdown [[notes/concepts/obsidian-markdown]]
- `%%comment%%` syntax hides content from rendered output — useful for agent notes and draft markers
- Task lists: `- [ ]` (open) and `- [x]` (checked) — rendered as interactive checkboxes in Obsidian
- Footnotes: `[^1]` inline, `[^1]: text` at bottom — rendered as superscript links
- Language identifiers on fenced code blocks enable syntax highlighting
