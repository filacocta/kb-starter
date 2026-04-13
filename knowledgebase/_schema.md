---
author: kb-bot
created: 2026-04-09
modified: 2026-04-10
type: meta
---

# KB Schema

This file is the complete specification for this knowledge base. Any agent — LLM, script, or human — should be able to manage this KB correctly by reading this file alone, with no prior training on this system.

---

## What This KB Is

A personal knowledge base that ingests sources (URLs, PDFs, transcripts, etc.) and synthesises them into a human-readable wiki. Notes are written to be useful without any AI assistant present. The structure is also optimised for LLM navigation: agents read compressed summaries first and dive deeper only when needed, keeping context windows efficient.

This KB is **LLM-agnostic**. All conventions are plain English. No model-specific syntax is used anywhere. Any capable LLM can act as an agent over this KB by following this schema.

---

## Safety Rule — Read Before Writing Anything

**You must never create or modify a file that already exists and does not have `author: kb-bot` in its YAML frontmatter.**

- If a file exists and lacks `author: kb-bot` → do not touch it. It belongs to the human.
- If a file does not yet exist → you may create it, and you must include `author: kb-bot` in its frontmatter.
- This rule has no exceptions. When in doubt, do not write.

---

## Folder Layout

```
knowledgebase/
  _schema.md          ← this file; the contract for all agents
  _index.md           ← navigation map listing all topics and projects
  _rules.md           ← behavioural rules for agents operating in this KB
  _checkpoint.md      ← written during long operations; used for resume after interruption
  _log.md             ← append-only activity log of all operations across the KB
  _dive-queue.md      ← cross-topic list of research rabbit holes and follow-up ideas flagged during sessions
  _todo.md            ← KB system development tasks (infrastructure, skills, tooling); not for research topics
  readinglist/
    Links.md          ← global URL inbox: dump URLs here without a topic; agent assigns and processes
    topics.md         ← broad topic interests without specific sources yet; feeds into _dive-queue.md
  topics/
    <topic-slug>/
      _summary.md     ← compressed overview of this topic (read this first)
      _discussion.md  ← append-only log of corrections and conversations
      sources/        ← one note per ingested source; immutable after initial write
      notes/
        concepts/     ← one article per concept
        entities/     ← people, organisations, tools (one file per entity)
        syntheses/    ← cross-cutting analyses spanning multiple concepts
      reading-list.md ← queue of URLs/files pending ingestion for this topic
  projects/
    <project-slug>/
      _summary.md     ← two sections: LLM orientation + user research direction
      notes/
```

**Naming conventions:**
- Topic and project slugs: lowercase, hyphen-separated (e.g. `large-language-models`, `kb-bot`)
- All system-generated files prefixed with `_`. Do not prefix user-created files with `_`.
- Source notes: `sources/YYYY-MM-DD-<slug>.md` or `sources/<author>-<year>-<short-title>.md` for academic papers
- Concept notes: `notes/concepts/<concept-slug>.md`
- Entity notes: `notes/entities/<entity-slug>.md`

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
status: draft | review | final   # omit on meta/index/summary files
tags: []
sources: []       # wikilinks [[sources/...]] this note draws from
related: []       # wikilinks to related notes in the KB
origin: ""        # source notes only: original URL or file path
---
```

**`type` values:**
| Type | Used for |
|---|---|
| `source` | One ingested source (URL, PDF, transcript, etc.) |
| `concept` | A synthesised wiki article on a concept |
| `entity` | A person, organisation, or tool |
| `synthesis` | Cross-cutting analysis spanning multiple concepts or sources |
| `output` | Filed answer to a user query |
| `summary` | A `_summary.md` folder overview |
| `index` | `_index.md` |
| `meta` | `_schema.md`, `_rules.md`, `_checkpoint.md`, `_log.md`, `_discussion.md`, `reading-list.md`, `_dive-queue.md` |

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
| Source citation | `[[sources/2026-04-09-some-article]]` | Citing the origin of a claim |
| Concept link | `[[notes/concepts/concept-slug]]` | Linking to a concept note in the same topic |
| Entity link | `[[notes/entities/entity-slug]]` | Linking to an entity note |
| Cross-topic link | `[[topics/other-topic/notes/concepts/concept]]` | Canonical concept in another topic |

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
Wikilinks to related notes: [[notes/concepts/related-concept]].
Source citations inline: [[sources/2026-04-09-origin]].

## See Also

- [[notes/concepts/related-a]] — brief reason for the link
- [[notes/entities/person-name]] — brief reason
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
- Do not modify source notes after their initial write (they are immutable)
- Do not fetch URLs or external content without explicit user approval
- Do not delete notes — mark them deprecated with `status: deprecated` in frontmatter instead
- Do not duplicate content across notes — link to canonical notes
- Do not use bullet-point dumps as the primary content of wiki notes
- Do not invent frontmatter fields not defined in this schema
- Always include `title` (matching the H1 heading) and `description` (one sentence) in every content note (`type: concept`, `entity`, `synthesis`, `source`)
