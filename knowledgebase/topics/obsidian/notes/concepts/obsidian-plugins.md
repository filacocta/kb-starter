---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: concept
topic: obsidian
title: "Obsidian Plugin Ecosystem"
description: "Obsidian's extensibility is one of its most significant practical advantages."
status: draft
tags: ["obsidian", "plugins", "community-plugins", "core-plugins", "extensions"]
sources: ["[[sources/2026-04-10-obsidian-help-overview]]", "[[sources/2026-04-10-phd20-dungeon-map-obsidian]]"]
related: ["[[notes/concepts/obsidian-overview]]", "[[notes/concepts/obsidian-dataview]]", "[[notes/concepts/obsidian-cli]]"]
---

# Obsidian Plugin Ecosystem

Obsidian's extensibility is one of its most significant practical advantages. The plugin system divides into **core plugins** (built in, maintained by the Obsidian team, toggleable) and **community plugins** (open-source, installed from the community directory, developed independently). As of early 2026, thousands of community plugins exist covering an enormous range of use cases.

## Core Plugins (Selected)

Core plugins ship with Obsidian and are disabled by default — enable in Settings > Core plugins:

| Plugin | What it does |
|---|---|
| **Daily Notes** | Creates/opens a dated note for today; required for `obsidian://daily` URI action |
| **Templates** | Inserts template files at cursor; simple variable substitution |
| **File Recovery** | Snapshots of notes at intervals — a safety net |
| **Graph View** | Interactive visualisation of the link graph |
| **Backlinks** | Shows all notes that link to the current note |
| **Outgoing Links** | Shows all links in the current note |
| **Search** | Full-text search across the vault |
| **Tag Pane** | Browse notes by tag |
| **Command Palette** | `Ctrl+P` launcher for all commands |
| **Slash Commands** | `/` command shortcuts in the editor |

## Key Community Plugins for This KB System

These are the community plugins most relevant to building and maintaining this knowledge base:

**[Dataview](https://obsidian.md/plugins/dataview)** — treats the vault as a queryable database. Essential for live indexes, tag filtering, and status dashboards. See [[notes/concepts/obsidian-dataview]].

**[Obsidian Git](https://obsidian.md/plugins/obsidian-git)** — auto-commits vault changes to git on a schedule and pushes to a remote. Safety net for AI-managed vaults; also enables version history and distribution via git.

**[Templater](https://obsidian.md/plugins/templater-obsidian)** — a more powerful template system than the core Templates plugin. Supports JavaScript execution, date logic, prompt-based input, and file-creation templates. Useful for creating consistently formatted notes.

**[Linter](https://obsidian.md/plugins/obsidian-linter)** — enforces consistent YAML frontmatter and heading formatting. Can auto-fix frontmatter on save — useful for keeping AI-written notes compliant with schema.

**[Leaflet](https://obsidian.md/plugins/obsidian-leaflet-plugin)** — renders interactive maps from image files inside notes. Markers can be linked to notes, images, or specific PDF pages. See the [[notes/syntheses/using-obsidian-strategies|dungeon map prep strategy]] for a concrete use case.

**[Local Images Plus](https://obsidian.md/plugins/obsidian-local-images-plus)** — downloads images referenced in notes to local storage. Prevents broken image links when offline or when external URLs change.

**[Web Clipper](https://obsidian.md/plugins/obsidian-web-clipper)** — browser extension for capturing web pages directly into the vault as markdown. Complements the KB ingest workflow for quick captures without going through a full `/kb-ingest` session.

**[Kanban](https://obsidian.md/plugins/obsidian-kanban)** — Kanban board view built from Markdown lists. Useful for project management within the vault.

**[Excalidraw](https://obsidian.md/plugins/obsidian-excalidraw-plugin)** — embeds a full whiteboard / diagramming tool. Notes can contain hand-drawn diagrams that are stored as text.

## Installing Community Plugins

Settings > Community plugins > Browse. Toggle "Restricted mode" off to enable community plugins (Obsidian prompts about this). Search, install, enable. Plugins install into `.obsidian/plugins/<plugin-id>/`.

## Plugin Safety

Community plugins have full access to the vault filesystem and can make network requests. Audit plugins before installing — check the source code (all community plugins must be open source) and look at the issue tracker. For AI-managed vaults like this KB, prefer plugins with a strong maintenance record.

## This KB's Own Plugin

The `kb-bot` plugin lives at `.obsidian/plugins/kb-bot/` in this vault. It provides the API integration layer between Obsidian and Claude (askClaude, modal UI, settings). Its source is in the separate `kb-bot` plugin repository. See the CLAUDE.md for the plugin's architecture.

## See Also

- [[notes/concepts/obsidian-dataview]] — the most important plugin for this KB
- [[notes/concepts/obsidian-overview]] — Obsidian architecture
- [[notes/syntheses/using-obsidian-strategies]] — use cases that specific plugins enable
