---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: summary
topic: obsidian
tags: ["obsidian", "pkm", "markdown", "plugins", "automation"]
---

# obsidian

*This summary is kept current as notes are added. Agents: read this before diving into sub-notes.*

## Overview

This topic documents Obsidian — the local-first, Markdown-based knowledge management application that serves as the storage and rendering layer for this entire KB system. Understanding Obsidian well is prerequisite for maintaining and extending the KB. Coverage includes: OFM syntax, the wikilink and embedding system, the `obsidian://` URI protocol that powers all citation deep-links, the CLI for automation, the Dataview plugin for live queries, the broader plugin ecosystem, and a growing synthesis of use-case strategies.

## Key Concepts

- [[notes/concepts/obsidian-overview]] — what Obsidian is; core philosophy (local-first, plain-text, extensible); vault structure
- [[notes/concepts/obsidian-markdown]] — full OFM reference: CommonMark + GFM + LaTeX + Obsidian extensions (highlights, callouts, wikilinks, embeds, comments)
- [[notes/concepts/obsidian-internal-links]] — wikilink system in depth: whole-note, heading, and block links; transclusion; display text; graph implications
- [[notes/concepts/obsidian-uri-protocol]] — `obsidian://` deep-link protocol; all six core actions; URI encoding; how KB citations are generated
- [[notes/concepts/obsidian-dataview]] — Dataview plugin: vault-as-database; DQL queries; TABLE/LIST/TASK/CALENDAR; DataviewJS
- [[notes/concepts/obsidian-cli]] — CLI tool for terminal-based vault control (stub — docs not yet ingested)
- [[notes/concepts/obsidian-plugins]] — plugin ecosystem: core plugins, key community plugins (Dataview, Git, Templater, Leaflet, Linter, Web Clipper)

## Synthesis

- [[notes/syntheses/using-obsidian-strategies]] — expanding collection of Obsidian use cases organised by goal: KB system, TTRPG prep, Zettelkasten, research writing, project management, GTD

## Sources

- [[sources/2026-04-10-obsidian-help-overview]] — Obsidian Help home page overview
- [[sources/2026-04-10-obsidian-basic-formatting]] — Basic formatting syntax reference
- [[sources/2026-04-10-obsidian-flavored-markdown]] — OFM specification
- [[sources/2026-04-10-obsidian-internal-links]] — Internal links documentation
- [[sources/2026-04-10-obsidian-uri]] — Obsidian URI protocol documentation
- [[sources/2026-04-10-phd20-dungeon-map-obsidian]] — [PHD20: Quick Dungeon Map Prep](https://phd20.com/blog/quick-dungeon-map-prep-obsidian/) — TTRPG strategy using Leaflet plugin

## Gaps & Open Questions

- CLI docs not yet ingested (JS-rendered page); stub concept note created
- Dataview official docs not yet ingested; stub concept note from internal knowledge
- Plugin development reference not yet ingested (relevant to kb-bot)
- No coverage yet of: Obsidian Sync/Publish, themes and CSS snippets, mobile app, canvas view
- `using-obsidian-strategies` synthesis has several stubs (Zettelkasten, academic writing, GTD) pending sources

---

*Last updated: 2026-04-10 — 6 concept notes, 1 synthesis note, 6 source notes*
