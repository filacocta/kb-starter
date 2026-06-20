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

Read `_schema.md`, `_rules.md`, and `_index.md`. **Do not** read `_rules-media.md` or `_schema-media.md` at startup — those are media-specific and are loaded per-item only when a source actually carries media.

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

If an approved item is a Wikipedia URL, treat it as an expansion item: fetch the article only to extract references/external links, then show the expanded cited-source list and get approval before processing those URLs. Preserve non-fetchable bibliographic citations as follow-up reading-list items. Do not write a source note for Wikipedia by default.

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

**0. Prep (before any fetch):** run `"<KB_ROOT>/_tools/kb-prep-item.py" --url "<url>"` and parse the JSON (dedup + Rule 30 mirror rewrite + media discovery in one read-only call). If `seen` is non-null, the URL is already ingested — mark the item `[x]` via `kb-checkoff.py` with note `already ingested → <path>` and skip to the next item; do not re-fetch. Otherwise keep `fetch_url`, `mirror_applied`, and `media` for steps 3–4. Entries in `errors` are non-fatal — log and continue.

1. **Announce:** Tell the user which item you are starting:
   > Processing (N of M): `<url>`

2. **Expand source context when applicable:**
   - Wikipedia URLs expand into cited-source URLs and bibliographic follow-ups. Process the approved cited sources as source notes and mark the Wikipedia item as expanded, not ingested, unless the user explicitly asked to ingest the article itself.
   - Other website URLs should be inspected for relevant same-context links exposed through interactables: in-article links, tabs, menus, buttons, downloads, methodology/data/documentation/about-this-project pages. Add approved companion pages to the current queue or topic reading list. Skip login, search, legal, tag, ad, comment, social, and generic navigation links.

3. **Fetch:**
   - Fetch the prep output's `fetch_url` (Rule 30 mirror rewrite already applied). When `mirror_applied` is true, record both URLs: canonical → `origin:`, mirror → `mirror:`. No prompting — the canonical URL was already approved by being in the queue.
   - For URLs, use WebFetch with this extraction prompt: *"Extract only the primary article or content body. Discard navigation, sidebars, headers, footers, cookie notices, ads, login prompts, comment sections, and all other boilerplate. Return clean plain text with headings and paragraph structure preserved."* For PDFs, try `markitdown "<path>"` first, fall back to `pdftotext "<path>" -`.
   - **For video items** (reading-list item tagged `[video]`, or a YouTube / x.com-video URL): do NOT WebFetch. Run `_tools/kb-video-analyze.py` and use its artifact (transcript + summary + slide OCR + figure descriptions + local keyframes) as the content — see the **"For a video URL"** branch in `/kb-ingest` Step 2 for the exact command and how it feeds the source note and media capture. In unattended/overnight batch runs, **omit `--vision` by default** (it spends Claude-plan usage per frame); add it only when the user has approved figure analysis for the batch.

4. **Media (from the step 0 prep output):** discovery already ran inside `kb-prep-item.py` — use its `media` array (no bytes downloaded; UI chrome pre-filtered; re-run `kb-media-capture.py --discover` without `--filter chrome` if the user asks what was dropped). If prep recorded a discovery error, log and continue — never block ingestion.

   **Load media metadocs only if any item in the queue actually has media.** Don't pre-load `_rules-media.md` and `_schema-media.md` at queue start — load them lazily the first time any item's `media` array is non-empty, then cache for the rest of the queue. Text-only items never trigger the media-rule load.

5. **Approve (combined per-source gate, Rule 4):** Apply the contextual relevance filter to discovered media, then present **one combined decision** covering media capture AND continuation:

   **Contextual relevance filter (applied before surfacing):** Default-skip book covers (infobox/sidebar of the article subject with no caption), advertisements and sponsor banners, author/byline/profile photos, generic publisher/journal/university logos, and social-media share-preview thumbnails. Default-include scientific figures, charts, diagrams, data tables rendered as images, documentary photographs that illustrate claims, maps, and screenshots of primary sources.

   **Single combined prompt format:**

   - If 0 items survive the filter:
     > Source N of M: `<title>` — fetched OK, no contextually-relevant media. Topic: `<topic>`.
     > Write source note and continue? (y / pause after this / stop / cancel this source)

   - If ≥1 item survives:
     > Source N of M: `<title>` — fetched OK. Topic: `<topic>`.
     > Discovered media (after contextual filter):
     >   - [a] https://example.com/figure-1.png — image, "Forecast graph"
     >   - [b] https://example.com/figure-2.png — image, "Histogram"
     > Capture which media + write source note + continue? (y=all media / list of letters / none / pause after this / stop / cancel this source)

   Default proposal: capture all items that survived the contextual filter, under `license: quoted-with-attribution`. The single answer covers media selection AND continuation, eliminating the previous double-gate.

6. **Capture approved media:**

   ```bash
   "<KB_ROOT>/_tools/kb-media-capture.py" --capture <url> [<url> ...] \
     --source-slug "<source-slug-without-date>" \
     --license quoted-with-attribution
   ```

   Pass the source's slug **without** the date prefix — the script prepends today's date automatically. Pass `--from-video <video-url>` for any image that was a video poster (the discovery JSON's `from_video` field).

   For each captured item: (a) write a media-description note at `topics/<topic>/notes/media/<descriptor-slug>.md` per `_media/_README.md`, (b) build a `media:` frontmatter block and `## Captured Media` section to splice into the source note before write, (c) run `"<KB_ROOT>/_tools/kb-link-media-backlinks.py" --media-note <path> --apply --top 1` to append a See Also backlink from the most thematically-relevant mutable concept/entity/synthesis note (Rule 11 freezes the source's `media:` block, so without this step the new media-desc note is graph-orphaned). Skip media-description creation for dedup hits; cross-reference the existing note instead.

7. **Assign topic:** If the item clearly belongs to a different topic than the queue's topic, confirm with the user before switching.

8. **Write source note:** Follow the same structure as `/kb-ingest` Step B exactly, including the `media:` frontmatter and `## Captured Media` section produced in step 6.

9. **Update reading list (locked):** mark the item processed via `"<KB_ROOT>/_tools/kb-checkoff.py" --file topics/<topic>/reading-list.md --url "<url>" --mark x --note "ingested <date> → [[knowledgebase/topics/<topic>/sources/<slug>]]"` (shared `.kb-write.lock`, per Rule 13). Append approved follow-up **URLs** under the `## Pending` section of `reading-list.md` (canonical section ladder in `_schema.md`: only Pending/Approved/Auto-approved/Processed/Failed — Retry permitted) and prose-only rabbit holes to `topics/<topic>/research-ideas.md` — never to `_dive-queue.md` (generated/read-only).

10. **Update checkpoint:** Mark item as done in `_checkpoint.md`, update `**Last updated:**`. Append captured-media URLs and `media_id` values under a "Captured Media" subsection.

11. **Completion announce (no separate continuation prompt):** Report what was written and move on automatically — the continuation decision was already taken at Step 5:

   > Done: `<title>` (N of M complete, M−N remaining)

   If the user answered Step 5 with `pause` or `stop`, honour that decision now: update `_checkpoint.md` status to `resumable`, write a one-line state record (which item was last completed), and stop without prompting again. Resume via `/kb-queue <topic>`.

   *Pause guidance:* The user can interrupt at any time mid-queue; the per-source checkpoint update at Step 9 makes any item resumable.

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
