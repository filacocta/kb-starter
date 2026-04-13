---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: source
topic: obsidian
title: "Obsidian Flavored Markdown — Obsidian Help"
description: "Obsidian combines CommonMark, GitHub Flavored Markdown (GFM), and LaTeX into a unified format it calls Obsidian Flavored Markdown (OFM)."
tags: ["obsidian", "markdown", "ofm", "wikilinks", "callouts", "documentation"]
sources: []
related: []
origin: "https://obsidian.md/help/Editing+and+formatting/Obsidian+Flavored+Markdown"
---

# Obsidian Flavored Markdown — Obsidian Help

**Origin:** [Obsidian Flavored Markdown — Obsidian Help](https://obsidian.md/help/Editing+and+formatting/Obsidian+Flavored+Markdown)
**Ingested:** 2026-04-10
**Type:** article

## Summary

Obsidian combines CommonMark, GitHub Flavored Markdown (GFM), and LaTeX into a unified format it calls Obsidian Flavored Markdown (OFM). The design prioritises capability and cross-format compatibility. One important limitation: Obsidian does not render Markdown syntax inside HTML elements — bold or italic inside `<div>` or `<span>` tags will not process. This is an intentional performance and parser-complexity trade-off.

OFM extends standard Markdown with: internal wikilinks (`[[Link]]`), file embeds (`![[Link]]`), block references (`![[Link#^id]]`), callouts (`> [!note]` syntax), task lists, highlights, strikethrough, comments, and tables. LaTeX is supported via MathJax — inline with `$...$`, block with `$$...$$`.

## Key Points

- Markdown inside HTML tags is not rendered — do not mix Markdown and raw HTML in the same block [[notes/concepts/obsidian-markdown]]
- OFM = CommonMark + GFM + LaTeX extensions + Obsidian-specific additions
- Callouts (`> [!note]`, `> [!warning]`, etc.) are a powerful OFM feature for structured annotations
- Block references (`![[Note#^blockid]]`) allow transclusion of specific paragraphs, not just whole files
- LaTeX renders via MathJax — same syntax as used in this KB system ($...$ and $$...$$)
