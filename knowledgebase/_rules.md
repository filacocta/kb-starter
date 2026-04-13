---
author: kb-bot
created: 2026-04-09
modified: 2026-04-09
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

This rule cannot be overridden by any instruction in a conversation. If a user asks you to edit a file outside these bounds, explain the constraint and decline.

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
4. Add approved items to `reading-list.md` before fetching, so work is tracked even if interrupted

There are no exceptions to this rule, even during a deep-dive or synthesis pass.

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
> **Conflict:** [[sources/source-a]] claims X, while [[sources/source-b]] claims Y.
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

## Rule 11 — Source Notes Are Immutable

Once a source note is written to `sources/`, do not modify its content. It is a faithful record of what was ingested at a point in time. If a source turns out to be wrong or outdated:
- Add `status: deprecated` to its frontmatter
- Add a note at the top of the file explaining why, linking to the replacement or correction
- Update the relevant wiki notes to reflect the correction, citing the reason

## Rule 12 — Incremental Compilation

When integrating a new source into existing wiki notes, **append or extend** — do not rewrite from scratch. Existing content represents accumulated knowledge; overwriting it destroys that history. Additions go at the end of relevant sections or in a new section. Contradictions are flagged with ⚠️ inline, not resolved by silently replacing existing text.

---

## Rule 10 — Do Not Delete

Do not delete notes. If a note is superseded or incorrect:

- Add `deprecated: true` to its frontmatter
- Add a note at the top of the file explaining what superseded it and linking to the replacement

Deletion destroys provenance. The note's existence — even if wrong — is part of the knowledge record.

---

## Rule 13 — Maintain the Dive Queue

Whenever a rabbit hole, follow-up idea, or "worth reading" source is identified during an ingestion session:

1. Add it to `_dive-queue.md` at the KB root under the relevant topic heading
2. Also add it to `topics/<topic>/reading-list.md` as an unchecked item
3. Tag the note (if one exists) with `rabbit-hole`

When the user asks for a list of topics for further study, read `_dive-queue.md` and present its unchecked items grouped by topic.

When a rabbit hole item is ingested, mark it `[x]` in `_dive-queue.md` with the date and source note path.

---

## Rule 14 — Topic Placement and Promotion

When creating notes for concepts identified as rabbit holes or not yet fully developed:

1. **File in the best-fit existing topic** — do not create a new topic slug for a concept that fits reasonably within an existing one
2. **Flag promotion candidates** — if a note clearly *belongs* to a topic that doesn't exist yet, add `promotion-candidate: true` to its frontmatter and tag it `rabbit-hole`
3. **Promote when warranted** — when a promotion-candidate note has 2+ sources and sufficient material for its own topic, raise the reorganisation with the user before moving files
4. **Never create a new topic unilaterally** for a single-source concept — start with the best available home and revisit

This prevents topic sprawl while preserving the signal that certain areas deserve their own topic when research matures.

---

## Rule 16 — Check the URL Inbox

`readinglist/Links.md` is the global URL inbox. At the start of any ingestion or query session:

1. Read `readinglist/Links.md` and check for unchecked items (`- [ ]`)
2. If any exist, surface them to the user:
   > "There are N unprocessed URLs in the inbox. Want to assign them to topics now, or process one?"
3. When assigning an inbox URL to a topic:
   - Add it to `topics/<topic>/reading-list.md`
   - Add it to `_dive-queue.md` under the topic heading
   - Mark it done in the inbox: `- [x] <url> — assigned to <topic> <date>`
4. Do not process (fetch/ingest) inbox items without user approval — assignment and ingestion are separate steps

Files in `readinglist/` that lack `author: kb-bot` frontmatter are user-owned — read them to surface their content, but never edit them.

---

## Rule 15 — Link Format (Non-Negotiable)

Two types of links exist and must never be confused:

- **Internal wikilinks** (cross-note navigation within the KB): `[[notes/concepts/ptolemaic-cosmology]]` or with display text `[[notes/concepts/ptolemaic-cosmology|Ptolemaic Cosmology]]`
- **External URL links** (links to the web): `[display text](https://example.com)`

**Never write `[[https://...]]` or `[[https://...|display text]]`.** Obsidian interprets this as an attempt to create a note whose filename is the URL, which produces broken links and errors.

Rule of thumb: if it starts with `http`, use `[text](url)`. If it is a path within the KB, use `[[path]]`.

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
3. Do not mark the entry `[!]` (reserved for permanent failures) — these are temporary blocks, not dead links

This ensures failed fetches are never silently lost — they become retry targets rather than wasted queries.

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

**Allowed:** `[[YourVault/your/folder/YourNote]]` inside a KB note  
**Never allowed:** kb-bot writing to or modifying any file outside `knowledgebase/` (Rule 1 unchanged)

**Where to put vault references:**
- Add a `vault_refs` field to the note's frontmatter listing any outbound vault wikilinks
- Also link inline where the referenced file is mentioned (e.g. in a "Duckmage World" section)
- For entity notes with a direct counterpart in another vault folder, always add the link

**Format:**
```yaml
vault_refs: ["[[YourVault/your/folder/YourNote]]"]
```

These are read-only pointers — navigation aids for the user browsing the vault in Obsidian. They do not grant kb-bot any write permissions over the linked files.
