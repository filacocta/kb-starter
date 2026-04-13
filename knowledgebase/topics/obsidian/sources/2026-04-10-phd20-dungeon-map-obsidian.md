---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: source
topic: obsidian
title: "Quick Dungeon Map Prep in Obsidian — PHD20"
description: "This blog post demonstrates a practical Obsidian workflow for tabletop RPG dungeon prep."
tags: ["obsidian", "use-case", "ttrpg", "leaflet", "maps", "dungeon-prep", "strategy"]
sources: []
related: []
origin: "https://phd20.com/blog/quick-dungeon-map-prep-obsidian/"
---

# Quick Dungeon Map Prep in Obsidian — PHD20

**Origin:** [Quick Dungeon Map Prep in Obsidian — PHD20](https://phd20.com/blog/quick-dungeon-map-prep-obsidian/)
**Ingested:** 2026-04-10
**Type:** article

## Summary

This blog post demonstrates a practical Obsidian workflow for tabletop RPG dungeon prep. The core technique uses the [Leaflet](https://obsidian.md/plugins/obsidian-leaflet-plugin) community plugin to render dungeon map images as interactive maps directly inside Obsidian. Each location on the map becomes a clickable marker linked to a note, image, or specific page within a PDF sourcebook.

The workflow: (1) install Leaflet, (2) add a dungeon map image to the vault, (3) create a note with a Leaflet code block referencing the image, (4) right-click to place markers at keyed locations, (5) link each marker to its corresponding content (screenshot of room description, PDF page reference, or note). The result is a navigable dungeon reference that combines visual spatial orientation with detailed source material.

PDF integration is particularly well-handled: markers can link directly to a specific page within a PDF using the syntax `filename.pdf#page=X`, so clicking a room marker opens the PDF at the exact page describing that room. Tooltip previews on hover can be enabled for quick reference without opening the linked content.

## Key Points

- Demonstrates Obsidian's viability as a TTRPG campaign management tool [[notes/syntheses/using-obsidian-strategies]]
- Leaflet plugin enables interactive map rendering inside vault notes — images become navigable
- PDF deep-linking (`filename.pdf#page=X`) works as a "Note to Open" value for Leaflet markers — this is a transferable pattern for any PDF-heavy workflow
- Hover tooltips provide quick previews without full navigation — useful for dense reference material
- Pattern is transferable: any workflow requiring spatial navigation + linked reference material (historical maps, architectural plans, research diagrams) could use this approach
