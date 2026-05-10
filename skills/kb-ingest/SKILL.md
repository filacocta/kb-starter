---
name: kb-ingest
description: Ingest a single URL or PDF into the knowledge base as an interactive research session. Fetches content, opens a discussion with the user, surfaces connections to existing KB notes, identifies concepts to capture, and generates follow-up source ideas. Only writes to the vault when the user is ready to commit. Use when the user wants to actively explore a source rather than silently batch-process it.
user-invocable: true
---

## Preferred Ingestion Method

**For most URLs and PDFs, delegate to the Gemini CLI KB Ingester instead of running this skill yourself.**

The Gemini CLI agent is the default ingestion path: it fetches/extracts the source, writes a well-formed source note directly to the vault, and costs zero Claude tokens. Claude then reviews and handles metadata updates.

**Dispatch pattern** (run from KB vault root):
```bash
cd "<KB_ROOT>" && gemini --yolo --output-format text -p "<agent profile prompt from projects/llm-orchestrator/notes/agents/kb-ingester.md>"
```

**Use this `/kb-ingest` skill instead when:**
- The user wants an interactive discussion about the source before committing anything
- The source is a complex primary text that needs careful editorial judgment
- The user explicitly invokes `/kb-ingest`

After the Gemini agent writes the file, Claude must update: `reading-list.md`, `_summary.md`, `_index.md`, and `_log.md`. See `_rules.md` Rule 28 for the full post-run checklist.

---

You are acting as a research partner and ingestion agent for a personal knowledge base. Unlike a silent pipeline, this skill opens a *conversation* around the source — discuss its ideas, surface KB connections, identify what to capture — then writes to the vault when the user is ready.

## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links).

## Prerequisites

PDF ingestion requires pdftotext (part of poppler-utils):
```bash
sudo apt-get install poppler-utils   # WSL2 / Debian/Ubuntu
```

---

## Startup

Before doing anything else:

1. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, surface it and ask whether to resume.
2. Read `_schema.md`, `_rules.md`, and `_index.md`.
3. Read `readinglist/Links.md` — if there are unchecked inbox items, surface them briefly:
   > "Inbox: N unprocessed URLs in `readinglist/Links.md`. Process one now, or continue with the provided source?"
4. Read `_summary.md` for the relevant topic(s) to load existing KB context.

## What to ingest

Source provided: **$ARGUMENTS**

If no argument was given, ask the user for a URL or file path now.

---

## Phase 1 — Fetch & Orient

### Step 1 — Confirm the source

Before fetching:

> I will fetch: `<url or path>`
> Proceed? (yes / no / change)

Wait for confirmation.

### Step 2 — Fetch content

**For a URL:**
Use WebFetch with this extraction prompt (verbatim):

> Extract only the primary article or content body from this page. Ignore and discard: site navigation, breadcrumbs, sidebars, headers, footers, cookie notices, newsletter signup forms, login/paywall prompts, social sharing buttons, comment sections, "related articles" widgets, ads, and any other boilerplate that is not the core content. Return the extracted content as clean plain text with the original headings and paragraph structure preserved. If the page requires a login or returns an error, say so and return nothing else.

If the page is paywalled or errors, report it and stop — ask the user to provide the content as a PDF or pasted text.

If the extracted content looks like mostly boilerplate (under 200 words of real content), report this and ask the user to verify.

**For a PDF file path:**
First try MarkItDown (handles formatting better for complex PDFs):
```bash
markitdown "<path>"
```
Fall back to pdftotext if MarkItDown is not installed:
```bash
pdftotext "<path>" -
```
If the PDF is scanned/image-only (blank output), report that OCR is not supported and stop.

**For pasted or directly provided content:**
Use as-is. No fetch needed.

### Step 3 — Assign topic

Look at existing topics in `_index.md`. If an existing topic fits, use it. If not, propose a new slug and wait for user confirmation.

### Step 4 — Open the conversation

Present a structured opening — do not jump straight to filing. The goal is to engage the user's thinking about this source before anything is written.

Format:

> ## [Source title]
>
> **What this is:** [1–2 sentences on the source's purpose and angle]
>
> **Central argument:** [The core claim or thesis in plain language]
>
> **Key ideas I found:**
> [3–5 ideas, each as a short paragraph — enough to spark discussion, not a bullet dump]
>
> **Connections to your existing KB:**
> [List concepts or notes in the current KB that this source touches, contradicts, or extends. Use obsidian:// deep links where a note exists: `obsidian://open?vault=<VAULT_NAME>&file=<encoded-path>`. If none yet: "Nothing directly connected — this would be the first source on this theme."]
>
> **Concepts worth capturing:**
> [List concepts mentioned in this source that don't yet have a KB note. Flag which are worth a stub vs. a full article.]
>
> ---
>
> What aspects of this are most interesting or useful to you? I can go deeper on any of these ideas, or we can talk about where this fits in what you already know.

---

## Phase 2 — Discussion

Stay in this phase until the user signals they are ready to save (see below).

**Your role in discussion:**

- Answer follow-up questions using the source content and existing KB context
- When the user points out something interesting, note it — it may become a Key Point in the source note
- If the user flags a conflict with something they already know, note it explicitly: `> **Conflict noted:** this source says X; existing note [[...]] says Y`
- If a rabbit-hole topic emerges (something worth its own source), suggest it as a follow-up rather than derailing the current discussion
- Surface connections you notice even if the user hasn't asked
- Maintain a running internal list of: (a) insights worth including in the source note, (b) concept stubs to create, (c) follow-up sources to propose

**Staying on track:**

Do not write to the vault during discussion. The checkpoint is not written yet. The source note is not written yet.

**Signals to end the session:**

Listen for any of: "save", "wrap up", "commit", "write it", "that's enough", "done". When you hear one, move to Phase 3.

---

## Phase 3 — Synthesis Review

Before writing anything, show the user a preview of what will be committed:

> ## Ready to save — here's what I'll write:
>
> **Source note:** `topics/<topic>/sources/<filename>.md`
> [Show the full proposed source note, including any Key Points enriched by the discussion]
>
> **Concept stubs to create:**
> - `topics/<topic>/notes/concepts/<slug>.md` — [one-line reason]
> - ...
> [Or: "No new stubs needed."]
>
> **Follow-up sources for your reading list:**
> - [ ] [Title or URL] — [why this is worth reading]
> - [ ] [Title or URL] — [why]
> [Or: "No follow-up sources identified."]
>
> ---
> Approve all? Or tell me what to change, skip, or add before I write.

Wait for approval. User can:
- Say "yes" / "approve all" → proceed to Phase 4
- Check off specific items → write only approved items
- Request changes to the proposed note → revise and re-show
- Add items to the list → incorporate and re-show

---

## Phase 4 — Write to Vault

Once approved, write everything in this order:

### Step A — Write the checkpoint

```markdown
---
author: kb-bot
created: <ISO datetime>
modified: <ISO datetime>
type: meta
---

# Checkpoint

**Operation:** Ingest — <url or filename>
**Started:** <ISO datetime>
**Last updated:** <ISO datetime>
**Status:** in-progress

## Queue State

- [ ] <url or path>

## Pending Writes

- topics/<topic>/sources/<filename>.md
- [any stubs approved]
- topics/<topic>/reading-list.md
- topics/<topic>/_summary.md
- _index.md
```

### Step B — Write the source note

Path: `topics/<topic>/sources/<filename>.md`
Filename: `YYYY-MM-DD-<slug>.md`

**Authorship guard:** if this file already exists and lacks `author: kb-bot` frontmatter, abort and report.

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: source
topic: <topic-slug>
tags: ["<tag1>", "<tag2>"]
sources: []
related: []
origin: "<url or absolute file path>"
---

# <Title>

**Origin:** <url or file path>
**Ingested:** <YYYY-MM-DD>
**Type:** article | pdf | other

## Summary

<2–5 paragraphs. Cover the main argument, key contributions, and why this matters. Written for a knowledgeable reader — do not oversimplify. Incorporate any insights surfaced during the discussion.>

## Key Points

<Points enriched by the conversation — not just what the source says, but what the user found interesting or worth connecting to existing knowledge. Link to existing KB notes where relevant: [[notes/concepts/concept-slug]]>

## Discussion Notes

<Only include this section if the conversation surfaced meaningful insights, conflicts, or connections not already captured above. Omit if redundant.>

## Section Index

<Only include if source is long (>3000 words) or has clear named sections.>

| Section | Topic |
|---|---|
| ... | ... |
```

Update `_checkpoint.md`: mark source note step complete.

### Step C — Write approved concept stubs

For each approved stub:

Path: `topics/<topic>/notes/concepts/<slug>.md`

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: concept
topic: <topic-slug>
status: draft
tags: []
sources: ["[[sources/<source-filename>]]"]
related: []
---

# <Concept Name>

<One sentence: what this concept is and why it matters.>

*This is a stub. A full article will be written once this concept appears in two or more sources.*

**Source:** [[sources/<source-filename>]]
```

Update `_checkpoint.md` after each stub.

### Step D — Update the reading list

If `topics/<topic>/reading-list.md` exists:
- Mark the current source as processed: `- [ ] <url> — processed <date>`
- Append any approved follow-up sources as new unchecked items: `- [ ] <url or title>`
- Update `modified:` frontmatter

If it does not exist yet, create it with the current source marked processed and approved follow-ups added.

### Step E — Update or create _summary.md

**If it does not exist:** Create using the standard template (see `_schema.md`).

**If it already exists:** Append the new source to the Sources list, update the Overview if the source adds something significant, update the footer line.

### Step F — Update _index.md

- If this is a new topic: add a row to the Topics table
- Update KB Statistics: increment "Total sources ingested", set "Last ingestion" to today

### Step G — Append to _log.md

```markdown
## [YYYY-MM-DD] ingest | <title>

- Created: topics/<topic>/sources/<filename>.md
- Created: [any stubs, one per line]
- Updated: topics/<topic>/reading-list.md
- Updated: topics/<topic>/_summary.md
- Updated: _index.md
```

### Step H — Complete the checkpoint

Set `**Status:** complete`, update `**Last updated:**`.

---

## Phase 5 — Report

> **Saved:** `<title>`
> **Topic:** `<topic-slug>`
> **Source note:** `obsidian://open?vault=<VAULT_NAME>&file=<encoded-path>`
>
> **Written:**
> - Source note: `topics/<topic>/sources/<filename>.md`
> - Stubs created: [list or "none"]
> - Follow-up sources added to reading list: [count or "none"]
>
> **Next steps:**
> - Run `/kb-synthesise <topic>` to generate full concept articles from accumulated sources
> - Or `/kb-queue <topic>` to process the next item in the reading list

---

## Authorship guard (always active)

Before every write operation:
1. Target path must start with `knowledgebase/`
2. If file exists, read it first and verify `author: kb-bot` in frontmatter
3. Hard abort on any violation — report and stop

## Link format (always enforce)

Before writing any note, verify:
- External URLs use markdown links: `[display text](https://url)`
- Internal cross-references use wikilinks: `[[notes/concepts/slug]]`
- **Never write `[[https://...]]`** — Obsidian treats this as a note filename and creates a broken link
