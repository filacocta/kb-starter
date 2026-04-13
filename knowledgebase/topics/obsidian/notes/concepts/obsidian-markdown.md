---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: concept
topic: obsidian
title: "Obsidian Flavored Markdown (OFM)"
description: "Obsidian uses a dialect of Markdown called Obsidian Flavored Markdown (OFM), which combines CommonMark, GitHub Flavored Markdown (GFM), and LaTeX with several Obsidian-specific ext"
status: draft
tags: ["obsidian", "markdown", "ofm", "formatting", "callouts", "latex"]
sources: ["[[sources/2026-04-10-obsidian-basic-formatting]]", "[[sources/2026-04-10-obsidian-flavored-markdown]]"]
related: ["[[notes/concepts/obsidian-overview]]", "[[notes/concepts/obsidian-internal-links]]"]
---

# Obsidian Flavored Markdown (OFM)

Obsidian uses a dialect of Markdown called **Obsidian Flavored Markdown (OFM)**, which combines [CommonMark](https://commonmark.org/), [GitHub Flavored Markdown (GFM)](https://github.github.com/gfm/), and [LaTeX](https://www.latex-project.org/) with several Obsidian-specific extensions. The goal is maximum capability while preserving plain-text portability — an OFM file is still a valid Markdown file that other tools can parse.

**Critical limitation:** Obsidian does not render Markdown syntax inside raw HTML elements. Bold, italic, wikilinks, etc. placed inside `<div>`, `<span>`, or other HTML tags will appear as literal text, not rendered formatting. Do not mix Markdown and raw HTML in the same block.

## Standard Markdown (CommonMark baseline)

| Element | Syntax |
|---|---|
| Headings | `# H1` through `###### H6` |
| Bold | `**text**` or `__text__` |
| Italic | `*text*` or `_text_` |
| Bold + italic | `***text***` |
| Inline code | `` `code` `` |
| Code block | ```` ```lang ... ``` ```` |
| Block quote | `> text` |
| Unordered list | `- item` or `* item` |
| Ordered list | `1. item` |
| Horizontal rule | `---` or `***` |
| External link | `[text](https://url)` |
| Footnote | `[^1]` inline; `[^1]: text` at bottom |
| Comment (hidden) | `%%hidden text%%` |

## GFM Extensions (included in OFM)

| Element | Syntax |
|---|---|
| Strikethrough | `~~text~~` |
| Task list | `- [ ]` open; `- [x]` done |
| Table | `\| col \| col \|` with `\|---\|---\|` separator |

## Obsidian-Specific Extensions

**Highlight:** `==text==` — renders as yellow highlight. Not in CommonMark or GFM; Obsidian-only.

**Internal wikilinks:** `[[Note name]]` — links to another note in the vault. See [[notes/concepts/obsidian-internal-links]] for the full system.

**File embeds:** `![[Note name]]` — transclude the entire content of another note inline. Works for notes, images, audio, video, and PDFs.

**Block references:** `![[Note#^blockid]]` — transclude a specific paragraph/block from another note.

**Callouts:** Extended block-quote syntax for structured annotations:
```
> [!note]
> Content of the callout.

> [!warning] Custom title
> Warning content here.
```
Callout types include: `note`, `tip`, `important`, `warning`, `caution`, `abstract`, `todo`, `success`, `question`, `failure`, `danger`, `bug`, `example`, `quote`. Callouts can be collapsed with `> [!note]-`.

**LaTeX math:** Rendered via MathJax. Inline: `$f(x) = \sum_{i=0}^{n} x_i$`. Block: `$$\nabla \cdot \mathbf{E} = \frac{\rho}{\varepsilon_0}$$`. This is what this KB uses for all mathematical notation.

## Paragraphs and Line Breaks

A blank line creates a new paragraph. A single Enter within a block creates a line continuation (same paragraph in rendered output). `Shift+Enter` or two trailing spaces create a forced line break within a paragraph.

## Comments

`%%comment text%%` hides content from the rendered view. Useful for: draft notes, agent annotations, TODO markers, content not ready for display. This KB uses this sparingly for agent-internal notes.

## What Not to Use

- Raw HTML blocks — Markdown does not render inside them
- Non-standard extensions from other Markdown flavors that OFM doesn't support (e.g. definition lists in some flavors)
- `[[https://url]]` wikilink syntax for external URLs — **always use `[text](url)` for external links** (see `_rules.md` Rule 15)

## See Also

- [[notes/concepts/obsidian-internal-links]] — the wikilink and embedding system in depth
- [[notes/concepts/obsidian-overview]] — Obsidian as a whole
