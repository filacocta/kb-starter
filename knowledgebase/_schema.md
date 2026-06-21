---
author: kb-bot
created: 2026-04-09
modified: 2026-06-03
type: meta
---

# KB Schema

This file is the core specification for this knowledge base. Media-specific frontmatter (source-note `media:` blocks, `media-description` notes) lives in `_schema-media.md` and is loaded only when a source actually carries media; otherwise this file is sufficient. Any agent — LLM, script, or human — should be able to manage the non-media portion of this KB by reading this file alone, with no prior training on this system.

---

## What This KB Is

A personal knowledge base that ingests sources (URLs, PDFs, transcripts, etc.) and synthesises them into a human-readable wiki. Notes are written to be useful without any AI assistant present. The structure is also optimised for LLM navigation: agents read compressed summaries first and dive deeper only when needed, keeping context windows efficient.

This KB is **LLM-agnostic**. All conventions are plain English. No model-specific syntax is used anywhere. Any capable LLM can act as an agent over this KB by following this schema.

---

## Safety Rule — Read Before Writing Anything

**You must never create or modify a file that already exists and does not have `author: kb-bot` in its YAML frontmatter.**

- If a file exists and lacks `author: kb-bot` → do not touch it. It belongs to the human.
- If a file does not yet exist → you may create it, and you must include `author: kb-bot` in its frontmatter.
- The single sanctioned exception is the clippings flow defined in Rule 23: human-authored clipping files in `clippings/` may be *read* (not modified) as ingestion input. All other writes require kb-bot authorship.
- When in doubt, do not write.

---

## Folder Layout

```
knowledgebase/
  _schema.md          ← this file; the contract for all agents
  _templates/         ← reusable file templates for catalog, summary, etc. (see Rule 26)
  _index.md           ← navigation map listing all topics and projects
  _rules.md           ← behavioural rules for agents operating in this KB
  _checkpoint.md      ← written during long operations; used for resume after interruption
  _log.md             ← append-only activity log of all operations across the KB
  _dive-queue.md      ← generated/read-only cross-topic dashboard derived from topic Auto-approved queues and research-ideas files
  _todo.md            ← KB system development tasks (infrastructure, skills, tooling); not for research topics
  ideas/
    _backlog.md       ← durable parking lot for broad side-project/inspiration ideas that are not yet topics or projects
  _media/             ← captured source media, stored with human-readable filenames and hash metadata
    _README.md        ← media capture policy, licensing posture, and path conventions
    images/           ← images stored as images/YYYY/MM/<date>-<description>-<short-hash>.<ext>
    videos/           ← videos stored as videos/YYYY/MM/<date>-<description>-<short-hash>.<ext>
    audio/            ← audio stored as audio/YYYY/MM/<date>-<description>-<short-hash>.<ext>
  readinglist/
    Links.md          ← global URL inbox: dump URLs here without a topic; agent assigns and processes
    topics.md         ← broad topic interests without specific sources yet; triaged into topic reading-lists / research-ideas
  topics/
    <topic-slug>/
      _summary.md     ← compressed overview of this topic (read this first)
      _discussion.md  ← append-only log of corrections and conversations
      _prospects.md   ← optional; project ideas, todos, and experiment seeds for this topic
      research-ideas.md ← optional; prose-only rabbit holes and research questions that do not yet have exact source URLs
      sources/        ← one note per ingested source; immutable after initial write
      notes/
        concepts/     ← one article per concept
        entities/     ← people, organisations, tools (one file per entity)
        media/        ← OCR, transcripts, visual descriptions, and analysis for captured media
        syntheses/    ← cross-cutting analyses spanning multiple concepts
      reading-list.md ← queue of URLs/files pending ingestion for this topic; `## Auto-approved` is the headless-only lane
  projects/
    <project-slug>/
      _summary.md     ← two sections: LLM orientation + user research direction
      _todos.md       ← kanban task board; human + agent editable (see Rule 28)
      notes/
```

**Naming conventions:**
- Topic and project slugs: lowercase, hyphen-separated (e.g. `large-language-models`, `kb-bot`)
- All system-generated files prefixed with `_`. Do not prefix user-created files with `_`.
- Source notes: `sources/YYYY-MM-DD-<slug>.md` or `sources/<author>-<year>-<short-title>.md` for academic papers
- Concept notes: `notes/concepts/<concept-slug>.md`
- Entity notes: `notes/entities/<entity-slug>.md`
- Media-description notes: `notes/media/<sha256-or-short-slug>.md`

---

## Frontmatter Schema

Every file created by an agent must include this YAML frontmatter block.

```yaml
---
author: kb-bot
created: <ISO-8601 date>
modified: <ISO-8601 date, updated on every edit>
type: <see types below>
topic: <topic-slug or project-slug>
title: <human-readable title, matches the H1 heading>
description: <one sentence — what this note is about; shown in KB Browser>
status: draft | review | final | deprecated   # omit on meta/index/summary files; `deprecated` is the canonical end-state per Rule 10 and Rule 11, always paired with `superseded_by:` when a successor exists
tags: []
sources: []       # wikilinks [[knowledgebase/topics/<topic>/sources/...]] this note draws from
related: []       # wikilinks to related notes in the KB
origin: ""        # source notes only: original URL or file path
ingest_status: succeeded | failed   # source notes only: omit or use succeeded for normal ingests; use failed for attempted fetch records that must not count as already ingested
media: []         # source notes only: captured or discovered media items; see Media Schema
---
```

**`type` values:**
| Type | Used for |
|---|---|
| `source` | One ingested source (URL, PDF, transcript, etc.) |
| `concept` | A synthesised wiki article on a concept |
| `entity` | A person, organisation, or tool |
| `media-description` | OCR, transcript, visual description, and analysis for a captured or discovered image, video, or audio item |
| `synthesis` | Cross-cutting analysis spanning multiple concepts or sources |
| `output` | Filed answer to a user query |
| `summary` | A `_summary.md` folder overview |
| `index` | `_index.md` |
| `prospects` | `_prospects.md` per topic — ideas, todos, experiment seeds, and project promotion records |
| `research-ideas` | `research-ideas.md` per topic — prose-only rabbit holes, research questions, and investigation prompts with no exact source URL yet |
| `catalog` | Living inventory of a project's instances (cards, creatures, mechanics, levels, etc.) — tracks what's implemented, what needs work, and what's on the wishlist. Named `catalog-<entity-type>.md` in `projects/<slug>/notes/`. See Rule 26. |
| `todos` | Kanban task board for a project (`_todos.md` at the project root). Four sections: Backlog, In Progress, Testing/Review, Done. Status markers: `[ ]` `[~]` `[?]` `[x]` `[!]`. See Rule 28 and template at `_templates/project-todos.md`. |
| `meta` | `_schema.md`, `_rules.md`, `_checkpoint.md`, `_log.md`, `_discussion.md`, `reading-list.md`, `_dive-queue.md`, `ideas/_backlog.md` |

---

## Topic Reading Lists

Topic reading lists are the canonical writable queues for source candidates. Every ingestion item lives under one of **five canonical level-2 sections** — the *section ladder*. Ad-hoc ingestion headings (`## Queue`, `## Approved Exact URL Queue`, `## Original Source Targets`, `## Future / not yet fetched`, project/batch headings, etc.) are **not permitted**; `_tools/kb-lint.py` flags any non-canonical ingestion heading. This is a cross-agent contract: all LLMs and all `kb-*` skills follow the same ladder.

| Section | Meaning | Who may process it |
|---|---|---|
| `## Pending` | Captured but **not yet vetted**. Needs a human (or interactive agent) to confirm it is worth ingesting. | Triage / review only — not an ingestion lane |
| `## Approved` | Vetted and good; will be ingested **interactively** (`/kb-queue`, `/kb-ingest`, human present). | Interactive agents after Rule 4 approval |
| `## Auto-approved` | `## Approved` **and** explicitly cleared for **unattended headless** ingestion (`/kb-auto`, kb-feed trusted lane). A strict subset of Approved. | `/kb-auto` and other headless agents |
| `## Processed` | Ingested. Items marked `[x]` with date + source-note path. | Terminal |
| `## Failed — Retry` | Fetch/ingest failed; awaiting manual retry (Rule 18). | Interactive |

**Promotion is upward and explicit:** `Pending → Approved → Auto-approved`. Move an item between sections when promoting/demoting — never duplicate the same URL across sections. Perform the move with `_tools/kb-promote.py` (shared write-lock, atomic; the sanctioned "move" verb, distinct from `kb-checkoff.py` which only flips the mark in place) — never hand-edit the section.

**The `## Auto-approved` gate.** Only the user, an interactive agent after explicit approval, or kb-feed under Rule 31's trusted-lane policy may place items here. Promote to `## Auto-approved` (rather than leaving in `## Approved`) only when the item is safe to fetch with **no human watching**: text-first, not paywalled, needs no interactive capture (OCR/video/figure selection/Wikipedia citation discovery), and comes from a trusted source. Everything else that is vetted stays in `## Approved` for interactive ingestion. `/kb-auto` and other unattended agents scan **only** `## Auto-approved`, stop at the next level-2 heading, and process only unchecked URL-first or local-PDF lines. Text-only items never belong in `## Auto-approved`.

Sub-grouping within a canonical section uses level-3 (`###`) headings (e.g. `### Foundational papers` under `## Pending`), which keeps the level-2 ladder intact for tooling. Non-ingestion material (prose rabbit holes, research questions) belongs in `research-ideas.md`, not the reading list.

Example:

```markdown
## Pending

- [ ] https://example.com/needs-triage — captured via #unfiltered, topic unconfirmed

## Approved

- [ ] https://example.com/paywalled-pdf — vetted; ingest interactively (needs login capture)

## Auto-approved

- [ ] https://example.com/source.pdf — vetted and headless-safe; next unattended `/kb-auto` pass

## Processed

## Failed — Retry
```

---

## Research Ideas

`topics/<topic>/research-ideas.md` stores prose-only rabbit holes and research questions that do not yet have an exact source URL or local source file. It is a mutable topic meta file with normal kb-bot frontmatter:

```yaml
---
author: kb-bot
created: <ISO-8601 date>
modified: <ISO-8601 date>
type: research-ideas
topic: <topic-slug>
title: "<Topic Title> — Research Ideas"
description: "Prose-only rabbit holes and research questions for the <topic-slug> topic."
---
```

Body format:

```markdown
# <Topic Title> — Research Ideas

Prose-only rabbit holes and research questions. Add exact URLs to `reading-list.md` instead.

## Ideas

- [ ] <idea> — <why it is worth investigating>
```

The root `_dive-queue.md` is generated from every topic's `## Auto-approved` section and `research-ideas.md`. Agents and scripts must not use `_dive-queue.md` as a writable queue.

---

## Media Schema → see `_schema-media.md`

The media-frontmatter spec for source notes and the `type: media-description` note format have moved to `_schema-media.md`. Load that file only when a source carries (or might carry) media. The note types `media-description` and the source-note `media:` field are valid kb-bot constructs — their full structural definition lives in `_schema-media.md`.

---

## Navigation Protocol

Agents must follow this order to use context efficiently:

1. Read `_index.md` — find the relevant topic(s)
2. Read `topics/<topic>/_summary.md` — decide if this topic is worth diving into
3. Read individual notes in `topics/<topic>/notes/` only as needed
4. Read source notes in `topics/<topic>/sources/` only when tracing a specific claim to its origin

**Never load all notes in a folder upfront.** The summary and index exist so you don't have to.

---

## Topic Placement and Promotion

When a rabbit hole concept or entity is first captured, file it in the **best-fit existing topic**, not a new one. Create a new topic slug only when:
- No existing topic is a plausible fit, **and**
- You have (or expect to have) 3+ sources for the new topic

Notes that may eventually belong to their own topic should be tagged `rabbit-hole` and flagged in their frontmatter as `promotion-candidate: true`. When a promotion-candidate note has 2+ sources and would benefit from its own topic, raise this with the user before restructuring.

---

## Page Creation Threshold

- **Stub** (`status: draft`, frontmatter + one-line definition + backlink to source): create when a concept or entity appears in **1 source**
- **Full article** (500–1,500 words, `status: draft` → `review` → `final`): create when a concept or entity appears in **2+ sources**
- **Never leave a wikilink pointing to a non-existent page.** Always create at least a stub.

---

## Link Conventions

All cross-references use Obsidian wikilinks:

| Link type | Format | When to use |
|---|---|---|
| Source citation | `[[knowledgebase/topics/<topic>/sources/2026-04-09-some-article]]` | Citing the origin of a claim |
| Concept link (same topic) | `[[knowledgebase/topics/<topic>/notes/concepts/concept-slug]]` | Linking to a concept note in the same topic |
| Entity link (same topic) | `[[knowledgebase/topics/<topic>/notes/entities/entity-slug]]` | Linking to an entity note |
| Cross-topic link | `[[knowledgebase/topics/other-topic/notes/concepts/concept]]` | Canonical concept in another topic |
| Cross-project link | `[[knowledgebase/projects/project-name/_summary]]` | Reference to a project |

**All wikilink paths must be vault-relative** — they start from the vault root (`knowledgebase/...`). Never use relative paths with `../`. Relative paths do not resolve correctly in Obsidian wikilinks and produce broken links.

- Wrong: `[[../../celestial-spheres/notes/concepts/florentine-neoplatonism]]`
- Correct: `[[knowledgebase/topics/celestial-spheres/notes/concepts/florentine-neoplatonism]]`

**Rules:**
- Every factual claim in a wiki note must have at least one source citation
- Link only the first occurrence of a concept per section
- Do not duplicate content — link to the canonical note instead
- Bold key terms on first use in each article

---

## Note Format

Concept notes (`type: concept`) follow this structure:

```markdown
---
[frontmatter]
---

# Concept Title

One or two sentences establishing what this concept is and why it matters.

## [Section heading]

Prose, not bullet dumps. Written for a knowledgeable reader.
Wikilinks to related notes: [[knowledgebase/topics/<topic>/notes/concepts/related-concept]].
Source citations inline: [[knowledgebase/topics/<topic>/sources/2026-04-09-origin]].

## See Also

- [[knowledgebase/topics/<topic>/notes/concepts/related-a]] — brief reason for the link
- [[knowledgebase/topics/<topic>/notes/entities/person-name]] — brief reason
```

**One central concept per note.** If a note covers two distinct concepts, split it.

---

## LaTeX Conventions

Mathematical and symbolic content must use LaTeX:

- Inline: `$f(x) = \sum_{i=0}^{n} x_i$`
- Block: `$$\nabla \cdot \mathbf{E} = \frac{\rho}{\varepsilon_0}$$`

LaTeX renders in Obsidian via the built-in MathJax renderer. Use standard LaTeX notation.

---

## Activity Log Format

`_log.md` is an append-only record of every operation. Append a new entry after each ingest, compile, query, or lint run. Never edit or delete existing entries.

```markdown
## [YYYY-MM-DD] <operation-type> | <brief description>

- Created: <file paths>
- Updated: <file paths>
- Errors: <any failures>
```

---

## What Agents Should Not Do

- Do not write to files outside `knowledgebase/`
- Do not modify files lacking `author: kb-bot` frontmatter
- Source-note body is immutable after initial write. Only the lifecycle-metadata exceptions defined in Rule 11 are permitted.
- Do not fetch URLs or external content without explicit user approval — subject to the sanctioned carve-outs: Rule 4 (headless-mode dive-queue items pre-approved by queue membership), Rule 29 headless exception (auto-capture of contextually-relevant media in `/kb-auto`), and Rule 30 (automatic transport-level rewrite to documented public mirrors for hosts that gate anonymous requests; the canonical URL is still the user-approved identifier)
- Do not delete notes — mark them deprecated with `status: deprecated` in frontmatter instead
- Do not duplicate content across notes — link to canonical notes
- Do not use bullet-point dumps as the primary content of wiki notes
- Do not invent frontmatter fields not defined in this schema
- Always include `title` (matching the H1 heading) and `description` (one sentence) in every content note (`type: concept`, `entity`, `synthesis`, `source`)
