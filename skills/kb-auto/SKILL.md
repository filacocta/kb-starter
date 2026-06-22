---
name: kb-auto
description: Headless autonomous KB ingestion. Processes URL-first items under the `## Auto-approved` heading of topic reading-lists (NOT the stale _dive-queue.md), one at a time, deduping against already-ingested sources before fetch, checkpointing after each item, and stopping cleanly at context limits. Designed to be run via a local cron/systemd timer firing `claude -p` for unattended overnight runs.
user-invocable: true
---

You are operating as a headless KB ingestion agent. You work autonomously — no pause points, no approval prompts per item. You checkpoint aggressively so any interruption is recoverable.

## Headless safety gate (non-negotiable)

These rules are what make unattended scheduling safe. Violating any of them can duplicate or runaway-ingest:

1. **Work source = `## Auto-approved` only.** Build your queue ONLY from unchecked items under a `## Auto-approved` heading in `topics/<topic>/reading-list.md`. Pre-approval = membership in that section. Ignore every other reading-list section and **ignore `_dive-queue.md` entirely** (it is stale/derived).
2. **URL-first items only.** Process an item only if it begins with an `http(s)://` URL (or a local PDF path). Skip prose/research-task lines — leave them untouched.
3. **Dedup before fetch.** For each candidate, run `"<KB_ROOT>/_tools/kb-prep-item.py" --url "<url>"`. If its `seen` field is a source-note path, the URL is already ingested: mark the item done with the dup note (step "check-off" below, note `already ingested → <path>`) and skip — do NOT fetch.
4. **No in-run expansion.** Do NOT follow Wikipedia citations, "related" links, or same-context companion pages within a run. If you discover worthwhile companions, append them to the topic `reading-list.md` as plain unchecked items **outside** `## Auto-approved` (they need human vetting) and move on. One queue item ⇒ at most one fetched source.
5. **All queue writes go through the lock.** Mark items done via `"<KB_ROOT>/_tools/kb-checkoff.py" --file <reading-list> --url <url> --mark x|! --note "..."` (it holds `.kb-write.lock`, re-reads, and writes atomically). Never hand-edit a queue line. Mark `[x]` only after the source note is written; `[!]` for permanent failure.
6. **One reading-list, no twin.** Reconcile only the topic `reading-list.md`. Do not write `_dive-queue.md`.

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

- If a topic slug is provided: process only that topic's `## Auto-approved` URL-first items
- If `all` or no argument: process `## Auto-approved` URL-first items across all topics, in order

---

## Startup

1. Read `_schema.md`, `_rules.md`, `_index.md`. **Do not** read `_rules-media.md` or `_schema-media.md` at startup — those are media-specific and are loaded per-item only when a source actually carries media (see step 3 of the Processing Loop).
2. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, resume from the recorded position rather than starting over.
3. Read `readinglist/Links.md` — if unchecked inbox items exist, **do not auto-assign them in headless mode** (Rule 16 reserves inbox assignment for an interactive session). Log the unprocessed inbox count to `_log.md` and continue with the dive-queue. Inbox items will be picked up next time `/kb-ingest` or `/kb-queue` is invoked interactively.
4. Build the work queue: scan each `topics/<topic>/reading-list.md` for unchecked URL-first items under a `## Auto-approved` heading (per the safety gate). Then run `"<KB_ROOT>/_tools/kb-seen.py" --build` once to refresh the dedup index. **Do not read `_dive-queue.md`.** If no `## Auto-approved` items exist anywhere, report "nothing auto-approved" and exit cleanly — this is the normal inert state until items are promoted.
5. Read each relevant `topics/<topic>/_summary.md` to load existing KB context.
6. Report what you found:

> **KB Auto — headless mode**
> Auto-approved queue: N URL-first items across M topics (after dedup)
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

**2. Prep + fetch** the source:
- **Prep first (safety gate rule 3):** run `"<KB_ROOT>/_tools/kb-prep-item.py" --url "<url>"` and parse the JSON — dedup, Rule 30 mirror rewrite, and media discovery in one read-only call. If `seen` is non-null, the URL is already ingested: check the item off with `kb-checkoff.py --mark x --note "already ingested → <path>"` and skip to the next item without fetching. Keep `media` for step 3. `errors` entries are non-fatal — log to checkpoint and continue.
- Fetch the prep output's `fetch_url`. When `mirror_applied` is true, record both URLs: canonical → `origin:`, mirror → `mirror:`. No prompting needed — headless carve-out.
- URLs: WebFetch with the standard boilerplate-stripping prompt (see below)
- PDFs: `markitdown "<path>"`, fall back to `pdftotext "<path>" -`
- If fetch fails (4xx/5xx, paywalled, blank): mark `[!]` in queue, log error in checkpoint, skip to next item

**No in-run expansion (safety gate rule 4).** Do not follow Wikipedia citations, "related" links, or same-context companion pages within this run.
- Wikipedia: if an `## Auto-approved` item is itself a Wikipedia URL, skip it in headless mode (citation discovery is an interactive task) — mark `[!]` with note "Wikipedia citation discovery is interactive-only".
- Companion pages you happen to notice: append them to the topic `reading-list.md` as plain unchecked items **outside** `## Auto-approved` (via kb-checkoff is not applicable — append a new line), so a human can vet/promote them later. Do not fetch them now. One queue item ⇒ at most one fetched source.

**WebFetch extraction prompt (use verbatim):**
> Extract only the primary article or content body from this page. Ignore and discard: site navigation, breadcrumbs, sidebars, headers, footers, cookie notices, newsletter signup forms, login/paywall prompts, social sharing buttons, comment sections, "related articles" widgets, ads, and any other boilerplate that is not the core content. Return the extracted content as clean plain text with the original headings and paragraph structure preserved. If the page requires a login or returns an error, say so and return nothing else.

**3. Auto-capture media** — discovery already ran inside step 2's `kb-prep-item.py` call (its `media` array, UI chrome pre-filtered); headless mode captures without prompting (per Rule 29 "Headless exception").

   **Load media metadocs lazily.** The first time any source's `media` array is non-empty, read `_rules-media.md` and `_schema-media.md` and cache them for the rest of the run. Sources whose pages are text-only never trigger the load. This keeps the per-item budget lean when most of the queue is text.

   **Contextual relevance filter (headless mode applies it automatically since there's no user gate):** From the discovered list, drop items that are not contextually relevant to the article's substantive claims — book covers, advertisements, author/byline photos, generic publisher/journal logos, social-media share-preview thumbnails. Capture only figures, charts, diagrams, data tables rendered as images, documentary photographs, maps, and screenshots of primary sources.

   Then capture every *contextually relevant* discovered image (and video poster) under `license: quoted-with-attribution`, capped at the Rule 29 default of 25 MB per source:

   ```bash
   "<KB_ROOT>/_tools/kb-media-capture.py" --capture <url1> [<url2> ...] \
     --source-slug "<source-slug-without-date>" \
     --license quoted-with-attribution
   ```

   Pass the source's slug **without** the date prefix — the script prepends today's date automatically. Pass `--from-video <video-url>` for any image that was a video poster (the discovery JSON's `from_video` field).

   For each captured item: write a media-description note at `topics/<topic>/notes/media/<descriptor-slug>.md`, build the `media:` frontmatter block and `## Captured Media` section to splice into the source note, then run `"<KB_ROOT>/_tools/kb-link-media-backlinks.py" --media-note <path> --apply --top 1` to backlink from the most thematically-relevant mutable concept/entity/synthesis note (Rule 11 freezes the source's `media:` block; without this step the new media-desc note is graph-orphaned). Skip media-description creation for dedup hits.

   If discovery or capture errors, log to checkpoint under an "Errors" section and continue — media is best-effort and must never block ingestion.

**4. Assign topic** — use the topic whose `reading-list.md` the item came from. If the content clearly belongs in a different topic, use that; no confirmation needed in headless mode.

**5. Write source note** — at `topics/<topic>/sources/YYYY-MM-DD-<slug>.md`. Follow the standard source note format from `_schema.md`, including the `media:` frontmatter and `## Captured Media` section from step 3. Authorship guard always active.

**6. Write concept stubs** — for each concept mentioned in the source that lacks an existing note, create a stub at `topics/<topic>/notes/concepts/<slug>.md`. Threshold: stub for 1 source, full article if concept already has a stub (2+ sources now).

**7. Check off the item (locked)** — mark it done in the topic `reading-list.md` via `"<KB_ROOT>/_tools/kb-checkoff.py" --file topics/<topic>/reading-list.md --url "<url>" --mark x --note "ingested YYYY-MM-DD → [[knowledgebase/topics/<topic>/sources/<slug>]]"`. This is the ONLY queue write.

**8. Do NOT touch `_dive-queue.md`** — it is derived/stale (safety gate rule 6); the step-7 check-off is the only reconciliation needed.

**9. Update `_summary.md`** — append new source to the Sources list, update the overview if significant, update the footer.

**10. Update checkpoint** — mark item complete, set next item. Record captured-media `media_id` values.

**11. Check context budget** — after each item, assess whether context is getting long. If approaching limits (roughly: you have processed 3+ items in this session, or the context feels full), stop the loop cleanly and write a final checkpoint status:

```markdown
**Status:** resumable
**Reason:** context limit approaching — resume with /kb-auto or next /schedule run
```

Then report: `Paused after N items. Checkpoint written. Next /schedule run will resume automatically.`

---

## After All Items Processed

1. Update `_index.md` statistics.
2. Append to `_log.md`, including a "Captured media" line per source (origin URL → media_id → local_path).
3. Run `kb-index --update` to refresh the frontmatter cache so `kb-search` stays current.
4. Set checkpoint `**Status:** complete`.
5. Report:

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
- Internal: `[[knowledgebase/topics/<topic-slug>/notes/concepts/slug]]`
- **Never `[[https://...]]`**

## Authorship Guard (always active)

Before every write:
1. Target path starts with `knowledgebase/`
2. If file exists, verify `author: kb-bot` in frontmatter
3. Hard abort on violation — log error, skip item, continue queue

---

## How to Schedule This Skill

To run autonomously on a recurring schedule, use a **local** cron or systemd-user timer that fires `claude -p` against the KB. **Do NOT use the `/schedule` skill** — it creates *remote cloud* agents that cannot reach the local KB, the local `kb-*` skills, or local media tooling.

Example (systemd-user timer ExecStart), run from `KB_ROOT`:
```
claude -p "Run kb-auto over the ## Auto-approved reading-list items" --permission-mode bypassPermissions
```

Each run picks up the next unchecked `## Auto-approved` item, dedups, processes it, and checkpoints. The persistent checkpoint means each run resumes where the last stopped.

To run within the current session on a repeating interval, use `/loop`:

```
/loop 10m /kb-auto
```

To stop: type "stop" in the session.
