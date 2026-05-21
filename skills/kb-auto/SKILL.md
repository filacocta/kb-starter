---
name: kb-auto
description: Headless autonomous KB ingestion. Reads _dive-queue.md, processes unchecked items one at a time without waiting for user input, checkpoints after each item, and stops cleanly at context limits. Designed to be run via /schedule (cron) or /loop for unattended overnight research runs. Pair with /schedule to auto-resume across sessions.
user-invocable: true
---

You are operating as a headless KB ingestion agent. You work through the research dive queue autonomously — no pause points, no approval prompts per item (items were pre-approved by being in the queue). You checkpoint aggressively so any interruption is recoverable.

## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links).

## Argument

**$ARGUMENTS**

- If a topic slug is provided: process only that topic's items from `_dive-queue.md`
- If `all` or no argument: process all unchecked items across all topics, in order

---

## Startup

1. Read `_schema.md`, `_rules.md`, `_index.md`.
2. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, resume from the recorded position rather than starting over.
3. Read `readinglist/Links.md` — if unchecked inbox items exist, assign them to topics automatically (use best-fit existing topic based on URL/description), add to topic reading-lists, mark done in inbox. Do not wait for user confirmation in headless mode — make a best-effort assignment and log what was assigned.
4. Read `_dive-queue.md` to build the work queue.
5. Read each relevant `topics/<topic>/_summary.md` to load existing KB context.
6. Report what you found:

> **KB Auto — headless mode**
> Queue: N items across M topics
> Resuming from: [checkpoint item, or "beginning"]
> Starting now.

---

## Processing Loop

For each unchecked item in the queue, in order:

### Per-item steps

**1. Update checkpoint** — mark current item as in-progress:

```markdown
**Status:** in-progress
**Current item:** <url or title>
**Last updated:** <ISO datetime>
```

**2. Fetch** the source:
- URLs: WebFetch with the standard boilerplate-stripping prompt (see below)
- PDFs: `markitdown "<path>"`, fall back to `pdftotext "<path>" -`
- If fetch fails (4xx/5xx, paywalled, blank): mark `[!]` in queue, log error in checkpoint, skip to next item

**WebFetch extraction prompt (use verbatim):**
> Extract only the primary article or content body from this page. Ignore and discard: site navigation, breadcrumbs, sidebars, headers, footers, cookie notices, newsletter signup forms, login/paywall prompts, social sharing buttons, comment sections, "related articles" widgets, ads, and any other boilerplate that is not the core content. Return the extracted content as clean plain text with the original headings and paragraph structure preserved. If the page requires a login or returns an error, say so and return nothing else.

**3. Assign topic** — use the topic recorded in `_dive-queue.md`. If it belongs in a different topic, use that; no confirmation needed in headless mode.

**4. Write source note** — at `topics/<topic>/sources/YYYY-MM-DD-<slug>.md`. Follow the standard source note format from `_schema.md`. Authorship guard always active.

**5. Write concept stubs** — for each concept mentioned in the source that lacks an existing note, create a stub at `topics/<topic>/notes/concepts/<slug>.md`. Threshold: stub for 1 source, full article if concept already has a stub (2+ sources now).

**6. Update reading list** — mark item processed in `topics/<topic>/reading-list.md`.

**7. Update `_dive-queue.md`** — mark item `[x]` with date and source note path.

**8. Update `_summary.md`** — append new source to the Sources list, update the overview if significant, update the footer.

**9. Update checkpoint** — mark item complete, set next item.

**10. Check context budget** — after each item, assess whether context is getting long. If approaching limits (roughly: you have processed 3+ items in this session, or the context feels full), stop the loop cleanly and write a final checkpoint status:

```markdown
**Status:** resumable
**Reason:** context limit approaching — resume with /kb-auto or next /schedule run
```

Then report: `Paused after N items. Checkpoint written. Next /schedule run will resume automatically.`

---

## After All Items Processed

1. Update `_index.md` statistics.
2. Append to `_log.md`.
3. Run `kb-index --update` to refresh the frontmatter cache so `kb-search` stays current.
4. Set checkpoint `**Status:** complete`.
4. Report:

> **KB Auto — complete**
> Processed: N sources
> Stubs created: N
> Topics updated: [list]
> Errors: [list or "none"]
>
> Run `/kb-synthesise <topic>` to generate full concept articles from accumulated sources.

---

## Link Format (always enforce)

- External URLs: `[display text](https://url)`
- Internal: `[[notes/concepts/slug]]`
- **Never `[[https://...]]`**

## Authorship Guard (always active)

Before every write:
1. Target path starts with `knowledgebase/`
2. If file exists, verify `author: kb-bot` in frontmatter
3. Hard abort on violation — log error, skip item, continue queue

---

## How to Schedule This Skill

To run autonomously on a recurring schedule, use `/schedule`:

```
/schedule "Run /kb-auto to process my KB research queue" --cron "0 * * * *"
```

This creates a remote agent that runs every hour, picks up the next unchecked item in `_dive-queue.md`, processes it, and checkpoints. Because the checkpoint persists between runs, each scheduled run continues exactly where the last one left off.

To run within the current session on a repeating interval, use `/loop`:

```
/loop 10m /kb-auto
```

To stop: type "stop" in the session.
