---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: concept
topic: obsidian
title: "Dataview"
description: "> *Agent inference — primary source fetch failed (path not found on publish server)."
status: draft
tags: ["obsidian", "dataview", "plugin", "query", "database", "frontmatter"]
sources: []
related: ["[[notes/concepts/obsidian-overview]]", "[[notes/concepts/obsidian-plugins]]", "[[notes/concepts/obsidian-markdown]]"]
---

# Dataview

> *Agent inference — primary source fetch failed (path not found on publish server). This note is written from prior knowledge. The Dataview documentation should be ingested from [https://blacksmithgu.github.io/obsidian-dataview/](https://blacksmithgu.github.io/obsidian-dataview/) when possible.*

**Dataview** is an Obsidian community plugin by Michael Brenan that treats the vault as a queryable database. Any metadata in a note's YAML frontmatter, inline fields, or filename is queryable. Dataview enables live dashboards, automatic indexes, and filtered views without manually curating lists — the query runs fresh every time the note is rendered.

## Core Concepts

Dataview's data model: every note in the vault is a *page* with implicit and explicit metadata. Implicit fields include `file.name`, `file.path`, `file.ctime`, `file.mtime`, `file.tags`, `file.inlinks`, `file.outlinks`. Explicit fields come from YAML frontmatter (`author`, `type`, `status`, `tags`, etc.) or inline Dataview syntax (`fieldname:: value`).

This KB's frontmatter fields (`type`, `topic`, `status`, `tags`, `sources`, `related`) are all queryable with Dataview.

## Query Types

Dataview supports four output types, used in fenced code blocks with `dataview` as the language:

**TABLE** — renders results as a table:
````
```dataview
TABLE topic, status, file.mtime AS "Modified"
FROM "knowledgebase/topics/celestial-spheres/notes"
WHERE type = "concept"
SORT file.mtime DESC
```
````

**LIST** — renders as a bulleted list:
````
```dataview
LIST
FROM #rabbit-hole
WHERE status = "draft"
```
````

**TASK** — renders task items across notes:
````
```dataview
TASK
FROM "knowledgebase"
WHERE !completed
```
````

**CALENDAR** — renders a calendar heatmap based on dates:
````
```dataview
CALENDAR file.mtime
FROM "knowledgebase"
```
````

## DQL (Dataview Query Language)

DQL follows a SQL-like structure:

```
TABLE|LIST|TASK|CALENDAR [fields]
FROM [source]
WHERE [condition]
SORT [field] ASC|DESC
LIMIT [number]
GROUP BY [field]
FLATTEN [field]
```

`FROM` accepts: folder paths (`"knowledgebase/topics"`), tags (`#rabbit-hole`), links (`[[Note]]`), and combinations with `AND`/`OR`/`-` (negation).

## Inline Queries

Backtick inline queries render values in prose:
- `` `= this.status` `` — renders the current note's `status` field
- `` `= [[Other Note]].author` `` — renders a field from a linked note

## DataviewJS

For complex logic, `dataviewjs` code blocks accept JavaScript with full access to the Dataview API:

````
```dataviewjs
const pages = dv.pages('"knowledgebase"').where(p => p.type === "concept");
dv.table(["Name", "Topic", "Status"], 
  pages.map(p => [p.file.link, p.topic, p.status]));
```
````

## Relevance to This KB

Dataview is the query layer that makes the `_dive-queue.md` tag-based browsing in `/kb-ask` option 3 possible. Instead of running `grep` against the filesystem, a Dataview query can find all notes tagged `rabbit-hole` live within Obsidian itself. It also enables: automatic topic indexes, status dashboards (all `draft` notes), source counts per topic, and broken-wikilink detection (notes with no inlinks).

**Recommended queries to add to this KB:**
- All rabbit-hole concept stubs, sorted by topic
- All source notes, sorted by ingestion date
- All concept notes with `status: draft`, no inlinks
- Topic coverage summary (count of notes per topic)

## See Also

- [[notes/concepts/obsidian-plugins]] — plugin ecosystem context
- [[notes/concepts/obsidian-overview]] — vault structure that Dataview queries
