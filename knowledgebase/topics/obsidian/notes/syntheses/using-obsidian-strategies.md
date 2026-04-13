---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: synthesis
topic: obsidian
title: "Using Obsidian — Strategies and Goals"
description: "This synthesis note collects different strategies for using Obsidian, organised by goal."
status: draft
tags: ["obsidian", "strategy", "use-case", "workflow", "pkm"]
sources: ["[[sources/2026-04-10-obsidian-help-overview]]", "[[sources/2026-04-10-phd20-dungeon-map-obsidian]]"]
related: ["[[notes/concepts/obsidian-overview]]", "[[notes/concepts/obsidian-plugins]]", "[[notes/concepts/obsidian-dataview]]", "[[notes/concepts/obsidian-internal-links]]"]
---

# Using Obsidian — Strategies and Goals

This synthesis note collects different strategies for using Obsidian, organised by goal. It is designed to expand over time as new use cases are ingested. Each strategy entry links to the source that demonstrated or described it. New entries should be appended with a source citation — do not rewrite existing entries.

---

## Strategy 1 — Personal Knowledge Base (This System)

**Goal:** Accumulate knowledge from heterogeneous sources into a searchable, cross-linked wiki that any LLM can navigate and contribute to.

**Key structures:**
- `_schema.md` as the agent contract — any LLM reads it to orient
- `topics/<topic>/` with `_summary.md` for index-first navigation
- Source notes (immutable) → concept notes (synthesised) → synthesis notes (cross-cutting)
- `_dive-queue.md` for tracking rabbit holes across sessions
- `_log.md` for append-only operation history

**Key plugins:** [[notes/concepts/obsidian-dataview|Dataview]] for live queries, Obsidian Git for safety and distribution, Linter for frontmatter enforcement.

**Key skills:** `/kb-ingest`, `/kb-ask`, `/kb-queue`, `/kb-auto`

**Strengths:** LLM-agnostic, provenance-preserving, context-efficient (summary-first navigation), portable.

*Source: this system itself — no external source yet*

---

## Strategy 2 — TTRPG Campaign and Dungeon Prep

**Goal:** Manage tabletop RPG campaign materials — maps, NPCs, locations, rules references, adventure sourcebooks — as a navigable linked system.

**Source:** [Quick Dungeon Map Prep in Obsidian — PHD20](https://phd20.com/blog/quick-dungeon-map-prep-obsidian/) [[sources/2026-04-10-phd20-dungeon-map-obsidian]]

**Core technique:** Use the **[Leaflet](https://obsidian.md/plugins/obsidian-leaflet-plugin)** community plugin to render dungeon/world map images as interactive maps with clickable markers. Each marker links to a note, image, or PDF page.

**Workflow:**
1. Add a map image to the vault
2. Create a note with a Leaflet code block (`id`, `image`, zoom params)
3. Right-click the rendered map → place markers at keyed locations
4. Set each marker's "Note to Open" to: a session note, an NPC note, or `sourcebook.pdf#page=47` for direct PDF reference
5. Enable hover tooltips for quick previews at the table

**PDF deep-link syntax:** `filename.pdf#page=X` in the "Note to Open" field opens the PDF at an exact page. This is a transferable pattern for any dense-reference workflow.

**Transferable to:** Historical map research (link map regions to source notes), architectural analysis (annotate floor plans), scientific diagrams (annotate regions of a chart or specimen image).

---

## Strategy 3 — Zettelkasten / Evergreen Notes

**Goal:** Build an atomic, densely linked note system where ideas compound over time.

*Stub — no source ingested yet. Key concepts: atomic notes (one idea per note), permanent notes vs. fleeting notes, literature notes as source summaries, evergreen notes as distilled ideas. Recommend ingesting: Sönke Ahrens, "How to Take Smart Notes" or the Zettelkasten.de documentation.*

**Relevant Obsidian features:** Graph view for seeing clusters, [[notes/concepts/obsidian-internal-links|wikilinks]] as the connective tissue, [[notes/concepts/obsidian-dataview|Dataview]] for finding orphan notes.

---

## Strategy 4 — Research and Academic Writing

**Goal:** Manage bibliography, reading notes, and drafts in one system; generate citations; maintain a literature review.

*Stub — no source ingested yet. Key plugins: Zotero integration (Citations plugin), Pandoc for export, Longform for manuscript management. Recommend ingesting: Bryan Jenks's academic Obsidian workflow.*

---

## Strategy 5 — Project and Task Management

**Goal:** Use Obsidian as a lightweight project management system with tasks, Kanban, and daily notes.

*Stub — no source ingested yet. Key plugins: Tasks (advanced task queries with due dates, recurrence), Kanban (board view from Markdown lists), Daily Notes for journal/standup. Recommend ingesting: the Obsidian Tasks documentation.*

---

## Strategy 6 — Second Brain / GTD

**Goal:** Capture everything (ideas, tasks, references, meeting notes) into a trusted system and process it regularly.

*Stub — no source ingested yet. Based on David Allen's Getting Things Done and Tiago Forte's Building a Second Brain (PARA method). Recommend ingesting: Forte's PARA description or Nick Milo's Linking Your Thinking framework.*

---

## Adding New Strategies

When ingesting a new source about Obsidian use cases, append a new entry here following the format:

```markdown
## Strategy N — [Name]

**Goal:** [one sentence]

**Source:** [link] [[sources/...]]

**Core technique:** [description]

**Workflow:** [numbered steps if applicable]

**Transferable to:** [other domains]
```

Mark the note `modified:` date and update `_summary.md`.
