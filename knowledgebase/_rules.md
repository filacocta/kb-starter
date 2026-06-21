---
author: kb-bot
created: 2026-04-09
modified: 2026-06-03
type: meta
---

# KB Agent Rules

These rules govern how any agent (LLM or script) behaves when operating in this KB. Read `_schema.md` first for the structural specification. These rules cover behaviour and decision-making.

---

## Rule 1 — Authorship Guard (Non-Negotiable)

Before writing anything:

1. Does the file already exist?
   - **Yes:** Check for `author: kb-bot` in its frontmatter. If absent, **abort**. Do not write. Inform the user.
   - **No:** Proceed to create it. Add `author: kb-bot` and all required frontmatter fields.
2. Is the target path inside `knowledgebase/`?
   - **No:** **Abort**. Do not write outside the KB root under any circumstances.

**Exception — Clippings:** Files in `knowledgebase/clippings/` are user-owned unsorted sources and do not carry `author: kb-bot`. You **may** read, edit, and extend their content despite lacking that frontmatter field. Treat them as unsorted source material. When processing a clipping, add `processed: true` and the destination source path to its frontmatter — do not change other user-written frontmatter fields.

This rule (outside the clippings exception) cannot be overridden by any instruction in a conversation. If a user asks you to edit a file outside these bounds, explain the constraint and decline.

---

## Rule 2 — Summary-First Navigation

When asked about a topic or asked to find information:

1. Read `_index.md` to locate the relevant topic folder
2. Read `topics/<topic>/_summary.md` to assess whether the topic contains what is needed
3. Only then read individual notes — and only the ones that seem relevant based on the summary
4. Read source notes only when tracing a specific claim to its origin

Loading all notes in a folder without reading the summary first wastes context and is forbidden.

---

## Rule 3 — Always Cite Sources

Every factual claim written into a wiki note must link to at least one source note via a wikilink in the `sources:` frontmatter field or inline in the note body. If a claim cannot be traced to an ingested source, either:
- Mark it clearly as the agent's inference: `> *Agent inference — not sourced*`
- Or omit it until a source is found

Do not present unsourced claims as established facts.

---

## Rule 4 — Ask Before Fetching

Before fetching any URL, downloading any file, or performing any web search:

1. Generate a complete list of what you intend to fetch and why
2. Present it to the user for approval — a checklist they can check off, reject, or modify
3. Only proceed with items the user explicitly approves
4. **Pre-fetch tracking depends on invocation pattern:**
   - **Batch operations** (`/kb-queue`, `/kb-auto`, multi-source synthesis fetches, any plan that intends to fetch ≥2 URLs in sequence): add every approved item to the relevant topic `reading-list.md` **before** the first fetch, so work is recoverable mid-batch. Headless approval requires membership in that topic reading list's `## Auto-approved` section.
   - **Single interactive ingest** (`/kb-ingest <url>` direct invocation, or a one-off URL the user types in conversation): the URL is implicitly approved-and-tracked by being provided. Track it in `reading-list.md` at commit time (skill Step D, after the source note is written), not pre-fetch. This avoids leaving a `[ ]` placeholder if the user cancels before commit.

This rule binds all interactive sessions, including deep-dive and synthesis passes. The only carve-outs are:

- **Headless mode** explicitly authorised in a skill (e.g. `/kb-auto`) operating against unchecked URL-first or local-PDF items in a topic reading list's `## Auto-approved` section. See Rule 29 "Headless exception" in `_rules-media.md` for the media-capture variant. `_dive-queue.md` is a generated dashboard and is not an approval record.
- **Transport-level URL rewriting** governed by Rule 30 in `_rules-media.md` (mirror domains for blocked hosts). The canonical URL is what the user approves; the mirror is the operational route to it.

Any other exception requires updating this rule first.

---

## Rule 5 — Checkpoint Long Operations

Any operation that will touch more than one file or take multiple steps must:

1. Write `_checkpoint.md` at the start, recording what will be done and in what order
2. Update `_checkpoint.md` after completing each step (mark item done, note next item)
3. On completion, update checkpoint status to `complete`

At the start of any session, check whether `_checkpoint.md` exists and has status `in-progress` or `resumable`. If so, surface it to the user and offer to resume before doing anything else.

---

## Rule 6 — Note Quality Standards

When writing wiki notes:

- **One concept per note.** If a note covers two distinct concepts, split it.
- **Prose, not bullets.** Notes are for humans to read. Use paragraphs with logical flow. Reserve bullet lists for enumerable items only (e.g. "three properties of X").
- **Link heavily.** Every concept that has its own note should be linked. Every source claim should cite its source note.
- **No duplication.** If a concept already has a canonical note, link to it — do not restate it.
- **LaTeX for all maths.** Inline `$...$` for expressions within text, block `$$...$$` for standalone equations.
- **Human-readable headings.** Headings should be meaningful to a reader who has never seen the KB before.

---

## Rule 7 — Corrections and Discussions

When a user flags an error or adds nuance during a conversation:

1. Locate the relevant note(s)
2. Apply the correction (checking authorship guard before writing)
3. Append the exchange to `topics/<topic>/_discussion.md` with a timestamp:

```markdown
## 2026-04-09 — Correction

**User:** [what the user said]
**Change made:** [what was updated and in which note]
```

Never overwrite `_discussion.md` — always append. The discussion log is a permanent record.

---

## Rule 8 — Uncertainty and Conflicts

When sources contradict each other:

1. Do not silently pick one. Note the conflict explicitly in the relevant wiki note:

```markdown
> **Conflict:** [[knowledgebase/topics/<topic>/sources/source-a]] claims X, while [[knowledgebase/topics/<topic>/sources/source-b]] claims Y.
> Resolution pending user input.
```

2. Add a note to `_discussion.md` flagging the conflict for the user to resolve.

When uncertain about where to place a note or what tags to assign, prefer the most specific existing topic/tag over creating a new one. If genuinely ambiguous, ask the user before writing.

---

## Rule 9 — Updating Summaries, Index, and Log

After writing or modifying any note in a topic folder:

1. Regenerate `topics/<topic>/_summary.md` to reflect the current state of the topic
2. If a new topic folder was created, update `_index.md` to include it
3. Append an entry to `_log.md` recording what operation was performed, what was created/updated, and any errors
4. Keep summaries compressed — they should convey the shape of the topic (key concepts, gaps, source count) in under 300 words. An agent should be able to read a summary and decide in one pass whether to dive deeper.

## Rule 11 — Source Notes Are Immutable (Body) — Mutable Lifecycle Metadata

Source-note body content is frozen at first write and must never be edited. It is a faithful record of what was ingested at a point in time.

The following narrow lifecycle changes are permitted after first write:

1. **Frontmatter:** add or toggle `status: deprecated` and add a `superseded_by:` wikilink. No other frontmatter fields may be edited after first write.
2. **Top-of-file banner:** insert a single blockquote immediately below the H1:
   ```markdown
   > ⚠️ **Deprecated <YYYY-MM-DD>:** <one-sentence reason>. Superseded by [[knowledgebase/topics/<topic>/sources/<replacement>]].
   ```
3. **Citing wiki notes:** update mutable wiki notes that cite the deprecated source so they reflect the correction. Those edits follow the normal authorship guard.

Deletion remains forbidden under Rule 10. The `media:` block on a source note remains frozen under Rule 29 (in `_rules-media.md`).

## Rule 12 — Incremental Compilation

When integrating a new source into existing wiki notes, **append or extend** — do not rewrite from scratch. Existing content represents accumulated knowledge; overwriting it destroys that history. Additions go at the end of relevant sections or in a new section. Contradictions are flagged with ⚠️ inline, not resolved by silently replacing existing text.

---

## Rule 10 — Do Not Delete

Do not delete notes. If a note is superseded or incorrect:

- Set `status: deprecated` in its frontmatter (same canonical format as Rule 11 source-note deprecation)
- Add `superseded_by: "[[knowledgebase/topics/<topic>/.../<replacement>]]"` to its frontmatter when a successor exists
- Add a top-of-file banner blockquote explaining what superseded it and linking to the replacement, e.g.:

  ```markdown
  > ⚠️ **Deprecated <YYYY-MM-DD>:** <one-sentence reason>. Superseded by [[knowledgebase/topics/<topic>/.../<replacement>]].
  ```

Deletion destroys provenance. The note's existence — even if wrong — is part of the knowledge record.

For **source notes** specifically, Rule 11 is the authoritative spec: only `status` and `superseded_by` frontmatter changes plus the banner blockquote are permitted; the source-note body is otherwise immutable.

---

## Rule 13 — Maintain Follow-Up Queues

Whenever a rabbit hole, follow-up idea, or "worth reading" source is identified during an ingestion session:

1. Add URL-first or local-file source candidates to `topics/<topic>/reading-list.md` as unchecked items under the appropriate canonical section ladder heading — `## Pending` if not yet vetted, `## Approved` if vetted for interactive ingestion (see the section-ladder spec in `_schema.md`; only these five level-2 headings are permitted: Pending, Approved, Auto-approved, Processed, Failed — Retry). Do not also write them to `_dive-queue.md`.
2. Add prose-only rabbit holes, research questions, and investigation ideas with no exact source URL to `topics/<topic>/research-ideas.md`.
3. Tag the note (if one exists) with `rabbit-hole`.
4. Treat `_dive-queue.md` as generated/read-only output from `_tools/kb-derive-dive-queue.py`; agents and scripts must not append to it directly.

When the user asks for a list of topics for further study, read the derived `_dive-queue.md` and present its unchecked items grouped by topic. If adding a new prose idea during that session, write it to the relevant topic's `research-ideas.md`, not `_dive-queue.md`.

When a reading-list item is ingested, mark it `[x]` in the topic `reading-list.md` with the date and source note path using `_tools/kb-checkoff.py` or an equivalent shared-lock path.

Marking and moving are distinct verbs over the shared write lock: `_tools/kb-checkoff.py` flips a line's checkbox **in place** (`[ ]`→`[x]/[!]/[~]/[?]`); `_tools/kb-promote.py` **moves** a `- [ ]` line between canonical ladder sections (`--from`/`--to`, e.g. `Pending → Approved → Auto-approved`). Use `kb-promote.py` for all section moves — never hand-edit the markdown to relocate an item. See `_tools/README.md` for the full endpoint reference.

---

## Rule 14 — Topic Placement and Promotion

When creating notes for concepts identified as rabbit holes or not yet fully developed:

1. **File in the best-fit existing topic** — do not create a new topic slug for a concept that fits reasonably within an existing one
2. **Flag promotion candidates** — if a note clearly *belongs* to a topic that doesn't exist yet, add `promotion-candidate: true` to its frontmatter and tag it `rabbit-hole`
3. **Promote when warranted** — when a promotion-candidate note has 2+ sources and sufficient material for its own topic, raise the reorganisation with the user before moving files
4. **Never create a new topic unilaterally** for a single-source concept — start with the best available home and revisit

This prevents topic sprawl while preserving the signal that certain areas deserve their own topic when research matures.

---

## Rule 14A — Durable Ideas Backlog

Broad side-project and inspiration-capture ideas that are not yet source-grounded research topics or active projects belong in `ideas/_backlog.md`. This file is a durable parking lot, not an ingestion queue: agents must not auto-fetch, auto-ingest, or auto-promote items from it.

When an item in `readinglist/topics.md` is checked off as routed to ideas/backlog, the original item must also be appended to `ideas/_backlog.md` under the shared KB write lock. Use `_tools/kb-checkoff.py --route-ideas-backlog` for future routed checkoffs. Do not mark a topic-interest item as "routed to ideas-backlog" unless the backlog append succeeds.

When an ideas-backlog item gains enough shape, promote it into the appropriate durable home:

1. Implementation/product ideas → `projects/<slug>/_summary.md` and `_todos.md`
2. Research domains → `topics/<slug>/research-ideas.md` or exact URLs in `topics/<slug>/reading-list.md`
3. Rejected ideas → keep the backlog item and mark it with a dated skip note

---

## Rule 16 — Check the URL Inbox

`readinglist/Links.md` is the global URL inbox. At the start of any ingestion or query session:

1. Read `readinglist/Links.md` and check for unchecked items (`- [ ]`)
2. If any exist, surface them to the user:
   > "There are N unprocessed URLs in the inbox. Want to assign them to topics now, or process one?"
3. When assigning an inbox URL to a topic:
   - Add it to `topics/<topic>/reading-list.md`
   - Move it into `## Auto-approved` only when the user or an interactive agent has explicitly approved unattended headless ingestion
   - Mark it done in the inbox: `- [x] <url> — assigned to <topic> <date>`
4. Do not process (fetch/ingest) inbox items without user approval — assignment and ingestion are separate steps

Files in `readinglist/` that lack `author: kb-bot` frontmatter are user-owned — read them to surface their content, but never edit them.

---

## Rule 15 — Link Format (Non-Negotiable)

Two types of links exist and must never be confused:

- **Internal wikilinks** (cross-note navigation within the KB): `[[knowledgebase/topics/celestial-spheres/notes/concepts/ptolemaic-cosmology]]` or with display text `[[knowledgebase/topics/celestial-spheres/notes/concepts/ptolemaic-cosmology|Ptolemaic Cosmology]]`
- **External URL links** (links to the web): `[display text](https://example.com)`

**Never write `[[https://...]]` or `[[https://...|display text]]`.** Obsidian interprets this as an attempt to create a note whose filename is the URL, which produces broken links and errors.

**Never use relative paths (`../`) in wikilinks.** Relative paths do not resolve correctly in Obsidian wikilinks and produce broken links. Always use vault-relative paths beginning from the vault root:

- Wrong: `[[../../celestial-spheres/notes/concepts/florentine-neoplatonism]]`
- Correct: `[[knowledgebase/topics/celestial-spheres/notes/concepts/florentine-neoplatonism]]`

This applies everywhere — frontmatter `related:` fields, inline links, and `See Also` sections. Cross-topic links always start with `knowledgebase/topics/<topic>/` or `knowledgebase/projects/<project>/`.

Rule of thumb: if it starts with `http`, use `[text](url)`. If it is a path within the KB, use `[[knowledgebase/...]]`.

---

## Rule 17 — Liberal Stub Creation (No Query Goes to Waste)

During any ingestion or research session, every named creature, concept, person, or tradition that surfaces — even tangentially — is a candidate for a stub. The default is to create it rather than ignore it.

Apply this rule when:
- A source mentions a related creature, figure, or tradition by name
- A search result surfaces a new entry the user hasn't asked about
- A concept appears that connects to existing KB notes

How to apply:
1. Create the stub at the appropriate path (`notes/entities/` or `notes/concepts/`) using the standard stub template
2. Link it from whichever note surfaced it (in the `related:` frontmatter or inline)
3. Add it to the topic's `_summary.md` if it warrants mention
4. Do **not** add every stub to `_dive-queue.md` — only add entries where there is a specific URL or named source worth fetching. Unnamed stubs stay as stubs until a source is identified.

The goal: research sessions leave behind a web of connected stubs that future sessions can expand. No named thing is a dead end.

---

## Rule 18 — Log Failed Fetches in the Relevant Source Note and Reading List

When a URL fetch fails (403, blank page, JS-rendered, redirect to wrong domain):

1. Note the failure in the source note of the article being written, under a **Failed sources** section:
   ```
   **Failed source:** https://example.com — reason (403 / JS-rendered / blank). Try browser PDF or direct access.
   ```
2. Add the URL as an unchecked item in the relevant topic's `reading-list.md` with a note explaining the failure and what to try
3. If a standalone source note is written to record an attempted-but-failed fetch, set `ingest_status: failed` in its frontmatter. `kb-seen.py` excludes these failure notes from dedup so the URL remains retryable. The failure paths in `kb-auto`, `kb-ingest`, and `kb-queue` must set this field whenever they create a failure source note.
4. Mark `[!]` only for **permanent** failures, where retrying the same URL is not expected to ever succeed. Examples:
   - All known mirrors have failed (per Rule 30's failure mode)
   - The page is hard-paywalled and no archival alternative exists
   - The URL returns 404/410/451 (dead, gone, or legally unavailable)
   - The host is dead (domain expired, server offline long-term)

   **Temporary** failures (transient 5xx, JS-rendering, intermittent blank responses, soft paywalls that may have an archival alternative) remain as `- [ ]` with a retry note — they are retry targets, not dead links.

This ensures failed fetches are never silently lost — they become retry targets when transient, and `[!]`-marked permanent failures when truly dead.

---

## Rule 20 — Capture KB-Ask Answers as Synthesis Notes

When a `/kb-ask` session produces a substantive answer — analysis comparing approaches, cross-cutting observations, design trade-offs, or answers to open questions — that answer must be written into the KB as a synthesis note before the session ends.

Process:
1. After answering, identify whether the answer contains novel cross-cutting analysis not already captured in any existing note
2. If yes: propose a synthesis note path (`topics/<topic>/notes/syntheses/<slug>.md`) and draft it
3. The synthesis note should stand alone — readable without the conversation that prompted it
4. Link it from the `## See Also` section of all directly relevant concept notes (append only)
5. Update `topics/<topic>/_summary.md` to include the new synthesis

The goal: no insight produced in a conversation is ephemeral. The KB grows with every substantive question answered.

---

## Rule 19 — Outbound Vault Links (Read-Only Cross-References)

KB notes MAY contain wikilinks pointing to any file anywhere in the vault, not just within `knowledgebase/`.

**Allowed:** `[[RPG/duckmage/world/cryptids/Mothman]]` inside a KB note  
**Never allowed:** kb-bot writing to or modifying any file outside `knowledgebase/` (Rule 1 unchanged)

**Where to put vault references:**
- Add a `vault_refs` field to the note's frontmatter listing any outbound vault wikilinks
- Also link inline where the referenced file is mentioned (e.g. in a "Duckmage World" section)
- For entity notes with a direct counterpart in another vault folder, always add the link

**Format:**
```yaml
vault_refs: ["[[RPG/duckmage/world/cryptids/Mothman]]"]
```

These are read-only pointers — navigation aids for the user browsing the vault in Obsidian. They do not grant kb-bot any write permissions over the linked files.

---

## Rule 21 — Active Cross-Topic Linking

When writing or updating any KB note, actively identify and create wikilinks to relevant notes in **other topics**, not just within the current topic.

**When to cross-link:**
- A concept in topic A has a counterpart, precursor, or descendant in topic B
- An entity appears in multiple topics (e.g. a person who spans several fields)
- A source in topic A explicitly references a concept whose canonical note is in topic B
- A synthesis note draws on material from multiple topics

**How to apply:**
1. In the `related:` frontmatter, include cross-topic wikilinks using full vault-rooted paths: `[[knowledgebase/topics/<other-topic>/notes/concepts/<slug>]]`
2. In the note body, link on first mention: `[[knowledgebase/topics/<other-topic>/notes/concepts/slug|Display Name]]`
3. If a concept note in topic A is the canonical home for something that appears in topic B, create a lightweight cross-reference stub in topic B pointing to the canonical note — do not duplicate content
4. When writing a synthesis note that spans topics, list all relevant topics in the frontmatter `tags` and link explicitly to notes in each

The goal: the KB is a graph, not a filing system. Cross-topic links are the edges that make it navigable as a whole rather than a collection of isolated silos.

---

## Rule 22 — Load Project Context Before Synthesis or Deep Work

Before any synthesis, deep `/kb-ask` session, or significant KB operation, **scan the Projects table in `_index.md` and load the `_summary.md` of any project that is semantically related to the topic or question at hand**.

**Why this matters:** Project summaries contain critical context that topic notes do not — what has already been built, what design decisions are locked, what technology is in use, and what gaps remain. Failing to read them before synthesis leads to redundant work, missed connections, and recommendations that contradict existing implementations.

**When to load a project summary:**
- The topic involves tools, plugins, or software that a project implements
- The topic involves a world, setting, or system that a project manages
- A synthesis note will produce recommendations or feature ideas that could be acted on within a project
- The user's question references a project by name or implies knowledge of one

**How to apply:**
1. After reading `_index.md`, scan the Projects table — use the Brief column (or read `_summary.md` if the brief is absent) to assess relevance
2. Read the `_summary.md` of any project with semantic overlap before beginning Phase 1 source reading
3. Treat project summaries as KB context, not as sources — cite them with `[[knowledgebase/projects/<name>/_summary]]` links, not as `[[knowledgebase/topics/<topic>/sources/...]]` entries
4. If a synthesis note produces implementation-ready ideas for a project, note it explicitly in the synthesis under a "Related Projects" section

---

## Rule 23 — Clippings Folder (Unsorted Web Sources)

`knowledgebase/clippings/` contains articles saved directly from the web by the user via Obsidian Web Clipper. These are **unsorted sources** — they have full content but have not been assigned to a topic or processed into source notes yet.

**Clipping frontmatter format** (written by Web Clipper, not by kb-bot):
```yaml
title: "Article Title"
source: "https://..."
author:
  - "Author Name"
published: YYYY-MM-DD
created: YYYY-MM-DD
tags:
  - "clippings"
```

**At the start of any session**, after checking `_checkpoint.md`, also check `clippings/` for unprocessed items:
1. `ls knowledgebase/clippings/` — list any files present
2. If files exist, surface them to the user:
   > "Clippings inbox: N unprocessed articles. Assign topics now, or process later?"

**Processing a clipping** means:
1. **Assign a topic** — identify the best-fit existing topic (or propose a new one)
2. **Write a source note** — create `topics/<topic>/sources/<YYYY-MM-DD-slug>.md` using the clipping's content; use the clipping's `source:` URL as the `origin:` field
3. **Update topic metadata** — `_summary.md`, `reading-list.md` (mark as processed), `_log.md`
4. **Move or tag the clipping** — once processed, add `processed: true` and the target source path to the clipping's frontmatter. Do **not** delete it (Rule 10).

**Authorship note:** Clipping files do NOT have `author: kb-bot` in frontmatter (they are user-owned). Per the Rule 1 exception, you **may** read, edit, and extend their content — treat them as unsorted source material.

---

## Rule 24 — Keep `_writing-prompts.md` in Sync

`knowledgebase/_writing-prompts.md` is the consolidated writing prompt dashboard. Whenever a `## Writing Prompts` section is added or modified in any KB note, **also update `_writing-prompts.md`** in the same operation.

**How to apply:**
1. After adding a `## Writing Prompts` section to any source note or project file, immediately open `_writing-prompts.md`
2. Add each new prompt as a bullet under the relevant theme section, in the format:
   ```
   - **[[essays/slug]]** — one sentence describing what to write
   ```
3. If no matching theme section exists, add one
4. Prompts in `_writing-prompts.md` should link to the source note they came from using the format: `**From [[knowledgebase/topics/topic/sources/note|Source Title]]:**`

This rule applies to all kb-bot operations that add writing prompts. The dashboard is only useful if it is complete — a prompt buried in a source note that does not appear in the dashboard is effectively invisible.

---

## Rule 25 — Prospects and Project Seeds

`knowledgebase/topics/<topic>/_prospects.md` is the ideas notebook for a topic — a lightweight file for collecting experiments, things to build, game ideas, todos, and anything that might eventually spin off into a full project.

**Item format:**
```
- [ ] **Idea title** — one-sentence description and reason it's worth doing
- [>] **Promoted idea** → [[knowledgebase/projects/<slug>/_summary]] — promoted YYYY-MM-DD
- [x] **Deferred/dropped idea** — brief reason
```

**Agent behaviour:**

1. **Adding an idea:** When the user mentions an experiment, thing to build, or "would be cool to" idea tied to a topic, offer to add it to that topic's `_prospects.md`. Create the file if it does not exist, using this template:

```markdown
---
author: kb-bot
created: <ISO-8601>
modified: <ISO-8601>
type: prospects
topic: <topic-slug>
---

# <Topic> — Prospects & Ideas

Ideas for projects, experiments, and things to build related to this topic.
To promote an idea to a full project, ask the KB bot — it will create the project summary and link it here.

## Ideas

## Promoted

## Deferred
```

2. **Surfacing prospects:** When working in a topic that has `_prospects.md`, surface open items (`- [ ]`) at the start of the session alongside clippings and reading-list items.

3. **Promoting a prospect to a project:**
   a. Create `knowledgebase/projects/<slug>/_summary.md` with `status: prospect` in frontmatter
   b. Mark the item in `_prospects.md`: change `[ ]` → `[>]` and append `→ [[knowledgebase/projects/<slug>/_summary]] — promoted <date>`
   c. Add the new project to the `## Projects` table in `_index.md` (note status as "Prospect" in the Brief column)
   d. Append to `_log.md`

4. **Visibility:** `_index.md` maintains a `## Prospects` section listing all topics that have `_prospects.md` files with at least one open item.

---

## Rule 26 — Catalog Files

Catalog files (`type: catalog`) are living inventories of a project's content instances — cards, creatures, mechanics, levels, items, abilities, or any other enumerable game/software entity. They complement entity spec notes (which define the data contract) by capturing the full set of instances in natural language, organised by implementation status.

**When to create a catalog:** When a project has 5+ instances of a recurring entity type (cards, creatures, items, levels), create a catalog for that type.

**File location and naming:** `projects/<slug>/notes/catalogs/catalog-<entity-type>.md`  
Use the canonical template at `knowledgebase/_templates/catalog.md`.

**Required sections (in order):**

1. `## ✅ Implemented` — entries that exist in the codebase/seed data and function correctly.
2. `## ⚠️ Needs Adjustment` — entries that exist but have known bugs, balance problems, missing features, or design gaps.
3. `## 💡 Ideas — To Add` — a free-form jotting area for future content.

**Entry format:** Each entry is a level-3 heading with the entity name. Include its ID in backticks, one-line natural language summary, and any design notes worth capturing. Do not just repeat field values — explain what the entity *does* and how it *feels* to play against or use.

**Maintenance:** Update catalog files whenever entities are added, changed, or removed.

**Template:** `knowledgebase/_templates/catalog.md`

---

## Rule 27 — Project Notes Subfolder Convention

When a project's `notes/` folder grows beyond approximately 10 files, reorganise into semantic subfolders rather than leaving it flat.

**Standard subfolders for game/software projects:**

| Subfolder | Contents | Naming convention |
|---|---|---|
| `entities/` | Data contract specs for named entity types | `entity-<name>.md` |
| `catalogs/` | Living inventories of content instances | `catalog-<entity-type>.md` |
| `impl/` | Technical implementation notes, architecture sketches, integration details | any |
| `design/` | Design documents, phase plans, roadmaps, todo lists | any |

Not all subfolders are required for every project. Create a subfolder only when it will hold 3+ files. A flat `notes/` folder is correct for projects with fewer than ~10 notes.

**When adding subfolders:**
1. Move files into the appropriate subfolder
2. Update all wikilinks in `_summary.md` and every note that references moved files
3. Update any external files (e.g. `CLAUDE.md` paths) that reference the old note paths
4. Append an entry to `_log.md` recording what was reorganised and why

---

## Rule 28 — Project Task Boards (`_todos.md`)

Every project folder (`projects/<slug>/`) must contain a `_todos.md` at its root. This is the canonical, human-editable task board for the project.

**File location:** `projects/<slug>/_todos.md`  
**Template:** `knowledgebase/_templates/project-todos.md`

**Structure:**

```
## 🗂️ Backlog        — not started; subdivided by Features / Bugs / Design Decisions / UX & Polish / Infrastructure
## 🔄 In Progress    — actively being worked on; each item should have a brief status note
## 🧪 Testing/Review — implemented but not yet verified end-to-end
## ✅ Done           — completed; keep entries with dates for project history
```

**Status markers (used inline in list items):**
- `[ ]` = backlog
- `[~]` = in progress
- `[?]` = needs testing or review
- `[x]` = done (include completion date in parens)
- `[!]` = blocked (include the blocker inline)

**Agent behaviour:**

1. **On project init** (`/kb-project-init`): create `_todos.md` from the template. Seed obvious backlog items from the project brief.
2. **On task completion** (KB update at session end): move the relevant item to `## ✅ Done` with today's date. Do not delete Done entries.
3. **On discovery of new work**: add it to the appropriate Backlog category immediately.
4. **On project summary scan**: surface any `[~]` (in progress) and `[!]` (blocked) items alongside the `_summary.md` content.
5. **Do not** move items to Done unless the user or a confirmed implementation verifies the work is actually complete.

**Note for users:** `_todos.md` is also yours to edit directly. The agent will respect your edits and never overwrite content you have added.

---

## Rules 29 & 30 — Media and Web Host Mirroring → see `_rules-media.md`

Rule 29 (Media Capture and Enrichment) and Rule 30 (Web Host Mirroring) have moved to `_rules-media.md`. Load that file only when a source carries (or might carry) media, or when fetching from a host that needs mirroring (e.g. `x.com`). This keeps the startup-prelude reads for core ingestion skills lean.

---

## Rule 31 — Telegram-Origin Capture Entries

kb-feed may write capture entries submitted through the user's private Telegram supergroup into KB queue files, provided all of these conditions are true:

1. The message came from the configured allowed chat and an allowlisted Telegram user.
2. The target lane was resolved from a persisted `message_thread_id -> topic_slug` mapping.
3. Any URL passed kb-feed validation and normalization before being written.
4. Every written line carries the standard Telegram annotation:

```markdown
- [ ] <item> — (via Telegram #<thread>, YYYY-MM-DD)
```

When the Telegram message includes both a URL and surrounding note text, kb-feed may preserve that note in the queue line:

```markdown
- [ ] <url> — <note text> — (via Telegram #<thread>, YYYY-MM-DD)
```

Lane behaviour:

1. **Mapped topic URL:** Append the URL to `topics/<topic>/reading-list.md` only. By default, append under an interactive queue section such as `## Pending` or `## Queue`; do not automatically mirror ordinary topic URLs into `_dive-queue.md`.
2. **Mapped topic URL with trusted-lane auto-approval:** kb-feed may append the URL under `## Auto-approved` only when the lane configuration explicitly sets `auto_approve = true`, the message came from the configured allowed chat and an allowlisted Telegram user, the message thread maps to the topic, the normalized item is URL-first, and URL validation/normalization succeeded. Text-only items are never auto-approved. Default lane behaviour is `auto_approve = false`.
3. **Mapped topic text without a URL:** Append the text to `topics/<topic>/research-ideas.md` under that topic as a research idea, follow-up, or project seed. This creates an investigation target, not a fetched source.
4. **`unfiltered` URL:** Append the URL to `readinglist/Links.md`, the global URL inbox. This is for URLs that do not yet have a confident topic assignment.
5. **`inspiration-capture` text:** Append the text to `readinglist/topics.md`, the broad topic-interest inbox. Later triage may propose one or more topic-specific research ideas, but the capture itself stays broad.
6. **Unmapped thread:** Do not append anything to KB markdown. kb-feed may record the event in its own local SQLite state and reply asking for a mapping.

This rule does not authorize fetching Telegram URLs, searching the web, downloading attachments, or processing non-text attachments. It also does not relax Rule 1: kb-feed may only write files with `author: kb-bot` frontmatter. A Telegram-origin item becomes pre-approved for headless ingestion only when it is intentionally placed in a topic reading list's `## Auto-approved` section under the trusted-lane rule above; ordinary topic reading-list entries and global inbox entries still follow the normal assignment and ingestion rules.
