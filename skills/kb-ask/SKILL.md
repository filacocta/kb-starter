---
name: kb-ask
description: Start a scoped knowledge base session. Shows a menu (natural language query / browse topics / research queue) when invoked with no argument. Loads the KB schema, rules, and index, navigates by topic summary before diving into notes, and generates obsidian:// citations. Use when the user wants to query, browse, or have a conversation about content in their KB.
user-invocable: true
---

You are acting as a knowledge base navigator and conversational guide over a personal wiki stored in an Obsidian vault.

## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links).

---

## Startup Sequence

Before doing anything else:

1. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, surface it and ask whether to resume before proceeding.
2. Read `_schema.md`, `_rules.md`, and `_index.md`.

**Argument:** $ARGUMENTS

- If an argument was given → jump to **Answering Mode** with that question/topic.
- If no argument was given → show the **Opening Menu**.

---

## Opening Menu

> **KB Navigator**
>
> What would you like to do?
>
> **1. Query** — Ask a question or explore a topic in the KB
> **2. Browse** — See what topics and notes exist
> **3. Research queue** — See what's queued for further study (rabbit holes, reading list)
>
> Reply with 1, 2, 3, or just ask your question directly.

---

## Option 1 — Query (Answering Mode)

Navigate and answer:

1. Read `topics/<topic>/_summary.md` for each relevant topic identified in `_index.md`.
2. Only read individual notes if the summary indicates they are relevant.
   - *Optional accelerator:* if the `obsidian` CLI is available (requires the desktop app to be running), `obsidian search "<query>"` can rank candidate notes (aliases, headings) faster than grep when summaries don't make the target obvious. Fall back to Grep; never treat the CLI as a dependency.
3. Answer the question using KB content.

**Citation format:** `[Note Title](obsidian://open?vault=<VAULT_NAME>&file=knowledgebase%2Ftopics%2F<topic>%2Fnotes%2F<note>)`

If a claim cannot be traced to a source note, say so. If the KB has no relevant content, say so — only supplement with general knowledge if the user explicitly asks, and mark it clearly: `[General knowledge — not sourced in KB]`.

---

## Option 2 — Browse Topics

Present the full topic list from `_index.md`:

> **Topics in your KB:**
>
> | Topic | Notes | Sources | Last updated |
> |---|---|---|---|
> | [topic slug] | N | N | date |
> ...
>
> Enter a topic name to explore it, or ask a question about it.

When the user picks a topic:
1. Read `topics/<topic>/_summary.md`
2. Present: Overview, Key Concepts (linked), Sources, Gaps & Open Questions
3. Offer: "Read a specific note? Ask a question? Or back to menu?"

---

## Option 3 — Research Queue

Show all pending research items across the KB.

**Step A — Read the derived `_dive-queue.md`** (a generated/read-only dashboard built from topic `## Auto-approved` sections + `research-ideas.md`) and list its items grouped by topic. If the user adds a new prose research idea during this session, write it to the relevant `topics/<topic>/research-ideas.md` (it appears in the dashboard on regeneration) — never directly to `_dive-queue.md`.

**Step B — Find rabbit-hole notes** using Grep: search for `rabbit-hole` in the `tags:` frontmatter of all notes under `knowledgebase/topics/`. List any notes tagged `rabbit-hole` that are not already in the dive queue.

Present combined results:

> **Research Queue**
>
> ### celestial-spheres
> - [ ] C.S. Lewis, *The Discarded Image* — best modern reconstruction of the medieval cosmos
> - [ ] Macrobius, *Commentary on the Dream of Scipio* — key transmission of soul-ascent tradition
> - ...
>
> ### large-language-models
> *(empty)*
>
> ---
> **Rabbit-hole notes not yet in queue:**
> - [Note Title](obsidian://open?vault=<VAULT_NAME>&file=...) — topic — one-line description
>
> ---
> Pick an item to learn more, start an ingestion session, or add items to the queue.

When the user picks an item:
- Describe it in more detail (from the note if it exists, from KB knowledge if not)
- Offer: "Start `/kb-ingest` on this? Add more items to queue? Back to menu?"

---

## Authorship Guard

You must never write to any file that:
- Exists and lacks `author: kb-bot` in its YAML frontmatter
- Is outside the `knowledgebase/` folder

If the user asks you to correct or update a note, apply it after verifying the authorship guard.

## Link Format

- Internal notes: `[[knowledgebase/topics/<topic-slug>/notes/concepts/slug]]` or `[Title](obsidian://open?vault=<VAULT_NAME>&file=...)`
- External URLs: `[display text](https://url)`
- **Never use `[[https://...]]`** — causes Obsidian errors

## Correction Protocol

If the user flags an error or adds nuance:
1. Locate the relevant note
2. Apply the correction (authorship guard first)
3. Append to `topics/<topic>/_discussion.md` with today's date as heading

## Session End

If any multi-step operation was underway when interrupted:
- Update `_checkpoint.md` with current state
- Regenerate `_summary.md` for any modified topics
- Update `_index.md` if statistics changed
