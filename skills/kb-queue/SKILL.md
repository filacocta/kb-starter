---
name: kb-queue
description: Batch process a topic's reading list in the knowledge base. Reads queued URLs/PDFs, presents an approval checklist, then ingests approved items one at a time with checkpointing and pause/resume support for overnight runs. Use when the user wants to process multiple sources at once or work through a backlog.
user-invocable: true
---

You are acting as a batch ingestion agent for a personal knowledge base. Your job is to work through a reading list, processing sources one at a time with full checkpoint support so runs can be interrupted and resumed across sessions.

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

PDF ingestion requires `pdftotext`:
```bash
sudo apt-get install poppler-utils   # WSL2 / Debian/Ubuntu
```

---

## Startup

**First, check for an existing checkpoint:**

Read `_checkpoint.md`. If it exists and has `**Status:** in-progress` or `**Status:** resumable`:

> I found an unfinished batch job:
> **Operation:** <operation from checkpoint>
> **Started:** <started>
> **Remaining items:**
> - [ ] item 1
> - [ ] item 2
>
> Resume this job? (yes / no — start fresh)

If the user says yes, skip to **Processing loop** using the remaining unchecked items.
If no, or no checkpoint exists, continue with the steps below.

---

## Setup

Read `_schema.md`, `_rules.md`, and `_index.md`.

**Check the URL inbox:** Read `readinglist/Links.md`. If there are unchecked items, surface them:
> "Inbox: N unprocessed URLs in `readinglist/Links.md`. Assign them to topics before processing? (yes / skip)"
If yes, go through each inbox URL, propose a topic assignment, wait for confirmation, then add to that topic's `reading-list.md` and mark done in the inbox.

**Argument:** $ARGUMENTS

- If a topic slug was provided, load `topics/<topic>/reading-list.md` as the queue source.
- If `inbox` was provided as the argument, process only the items currently in `readinglist/Links.md` (assign topics, then process).
- If no argument was given, ask: "Which topic's reading list should I process? (provide a topic slug, 'inbox' for inbox items, or 'all' for all topics)"

---

## Step 1 — Build the approval checklist

Read the relevant `reading-list.md` file(s). Extract all unchecked items (`- [ ]`).

Present the full list to the user:

> Here are the queued items for `<topic>`:
>
> Please check off the ones you want me to process now:
>
> - [ ] https://example.com/article-one
> - [ ] https://example.com/article-two
> - [ ] https://example.com/article-three
>
> Reply with the numbers or URLs you approve, or "all" to approve everything. Items you skip will remain in the queue.

Wait for the user's selection. Do not proceed until you have explicit approval.

---

## Step 2 — Write the checkpoint

Once the user has approved items, write `_checkpoint.md` before touching anything else:

```markdown
---
author: kb-bot
created: <ISO datetime>
modified: <ISO datetime>
type: meta
---

# Checkpoint

**Operation:** Queue ingestion — <topic>
**Started:** <ISO datetime>
**Last updated:** <ISO datetime>
**Status:** in-progress

## Queue State

- [ ] <approved url 1>
- [ ] <approved url 2>
...

## Pending Writes

Updating as items complete.
```

---

## Processing loop

For each approved item, in order:

### Per-item steps

1. **Announce:** Tell the user which item you are starting:
   > Processing (N of M): `<url>`

2. **Fetch:** For URLs, use WebFetch with this extraction prompt: *"Extract only the primary article or content body. Discard navigation, sidebars, headers, footers, cookie notices, ads, login prompts, comment sections, and all other boilerplate. Return clean plain text with headings and paragraph structure preserved."* For PDFs, try `markitdown "<path>"` first, fall back to `pdftotext "<path>" -`.

3. **Assign topic:** If the item clearly belongs to a different topic than the queue's topic, confirm with the user before switching.

4. **Write source note:** Follow the same structure as `/kb-ingest` Step 5 exactly.

5. **Update reading list:** Mark the item as processed in `reading-list.md`.

6. **Update checkpoint:** Mark item as done in `_checkpoint.md`, update `**Last updated:**`.

7. **Pause point:** After each item, report:
   > Done: `<title>`
   > Remaining: N items
   >
   > Continue to next item? (yes / pause / stop)
   >
   > *Tip: if you are approaching a usage limit, type "pause" — the checkpoint will let you resume in a new session.*

   - **yes** → continue to next item
   - **pause** → update checkpoint status to `resumable`, stop processing, tell user how to resume (`/kb-queue <topic>`)
   - **stop** → update checkpoint status to `resumable`, stop

---

## After all items are processed

1. Regenerate `_summary.md` for each affected topic
2. Update `_index.md` statistics
3. Mark checkpoint as `complete`
4. Report:

> **Batch complete.**
> Processed: N sources
> Topics updated: <list>
>
> Run `/kb-synthesise <topic>` to generate wiki notes from the new sources.

---

## Authorship guard (always active)

Before every write:
1. Target path must start with `knowledgebase/`
2. If file exists, verify `author: kb-bot` in frontmatter before modifying
3. Hard abort on any violation — report and stop

---

## Error handling

- **Fetch fails (4xx/5xx):** Log the failure in `_checkpoint.md` under a "Errors" section, mark the item with `- [!]` in the reading list, continue to next item
- **PDF is image-only:** Same as fetch fail — note it and continue
- **Usage limit approaching:** If you notice context getting long, proactively suggest pausing before the limit is hit
