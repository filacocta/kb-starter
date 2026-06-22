---
name: kb-ingest
description: Ingest a single URL or PDF into the knowledge base as an interactive research session. Fetches content, opens a discussion with the user, surfaces connections to existing KB notes, identifies concepts to capture, and generates follow-up source ideas. Only writes to the vault when the user is ready to commit. Use when the user wants to actively explore a source rather than silently batch-process it.
user-invocable: true
---

## Delegated ingestion — deferred

A delegated ingestion path (a cheaper external model fetches and drafts the source note; Claude reviews and does metadata) is planned but **not currently wired** — the previous Gemini CLI dispatch referenced an agent profile (`projects/llm-orchestrator/notes/agents/kb-ingester.md`) that does not exist, and the `gemini` CLI is not installed. Until the delegation layer is rebuilt (tracked in `_todo.md`), run this skill directly.

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
2. Read `_schema.md`, `_rules.md`, and `_index.md`. **Do not** read `_rules-media.md` or `_schema-media.md` at startup — those are media-specific and are loaded per-item only when a source actually carries media.
3. Read `readinglist/Links.md` — if there are unchecked inbox items, surface them briefly:
   > "Inbox: N unprocessed URLs in `readinglist/Links.md`. Process one now, or continue with the provided source?"
4. Read `_summary.md` for the relevant topic(s) to load existing KB context.

## What to ingest

Source provided: **$ARGUMENTS**

If no argument was given, ask the user for a URL or file path now.

---

## Phase 1 — Fetch & Orient

### Step 1 — Confirm the source

- **If the skill was invoked with a URL or file argument** (`/kb-ingest <url>`): the user already implicitly approved by typing the URL. Skip the explicit "Proceed?" prompt and proceed directly to Step 2. The commit-time gate at Phase 3 covers the substantive approval.
- **If the skill was invoked without an argument**: prompt for a URL or path, then proceed directly to Step 2 once the user provides one (no redundant second confirmation).
- **If the URL has obvious issues** (bare filename with no host, unsupported scheme, suspected typo): then *do* ask once before fetching.

### Step 1.5 — Expand source context

**For Wikipedia URLs:** Treat the article as a citation index, not the source of record. Fetch it only to extract references, external links, and cited-source metadata. Dedupe the cited URLs, label likely primary/reputable/low-value sources, and present the list for approval before fetching any cited source. Ingest approved cited sources as source notes; preserve non-fetchable bibliographic citations as follow-up reading-list items. Do not create a source note for the Wikipedia article unless the user explicitly asks. Keep the Wikipedia URL only as discovery provenance or a reading-list parent.

**For other website URLs:** Inspect relevant same-context links exposed through page interactables: in-article links, tabs, menus, buttons, "download", "methodology", "data", "about this project", "documentation", and similar controls. Present companion pages as follow-up or batch-ingest candidates before fetching anything beyond the approved URL. Skip login, search, legal, tag, ad, comment, social, and generic site-navigation links.

If the expansion list is large, group it by source type and recommend a first batch while preserving the rest as reading-list follow-ups.

### Step 2 — Fetch content

**Pre-fetch prep (one call).** Run `"<KB_ROOT>/_tools/kb-prep-item.py" --url "<url>"` and parse the JSON — it bundles the read-only pre-fetch steps (dedup check, Rule 30 mirror rewrite, media discovery):

- `seen` — non-null means the URL is already ingested at that source-note path. Tell the user (link the note) and ask whether to stop or re-ingest before proceeding.
- `fetch_url` / `mirror_applied` — the Rule 30 transport rewrite (x.com → fxtwitter etc.) is already applied. Fetch `fetch_url`; when `mirror_applied` is true, record canonical → `origin:` and mirror → `mirror:`. No prompting — the user approved the canonical URL; the mirror is transport.
- `media` — discovery results, no bytes downloaded, obvious UI chrome already filtered. Hold for Step 2.5.
- `errors` — non-fatal prep failures; log and continue, never block ingestion.

**For a URL:**
Use WebFetch with this extraction prompt (verbatim):

> Extract only the primary article or content body from this page. Ignore and discard: site navigation, breadcrumbs, sidebars, headers, footers, cookie notices, newsletter signup forms, login/paywall prompts, social sharing buttons, comment sections, "related articles" widgets, ads, and any other boilerplate that is not the core content. Return the extracted content as clean plain text with the original headings and paragraph structure preserved. If the page requires a login or returns an error, say so and return nothing else.

If the page is paywalled or errors, report it and stop — ask the user to provide the content as a PDF or pasted text.

If the extracted content looks like mostly boilerplate (under 200 words of real content), report this and ask the user to verify.

**For an x.com / twitter.com tweet (any tweet URL — with or without video, quote, or thread):**

Do NOT WebFetch x.com (it 302-redirects to a JS page WebFetch can't read). Use the tweet-fetch tool — it returns the FULL text (not the truncated preview), the quoted tweet's full text + media, the self-reply thread (walked upward), and a flat `video_urls` list:

```bash
"<KB_ROOT>/_tools/.venv/bin/python" "<KB_ROOT>/_tools/kb-tweet-fetch.py" "<url>" --json > /tmp/kbtf-<slug>.json
```

Parse the JSON: `main` (text, author, photos[], videos[]), `quote` (full quoted tweet or null), `thread[]` (the full **unrolled forward** self-reply chain via fxtwitter `/2/thread`; length 1 if not a thread), `video_urls` (all videos across main+quote+thread), and flags `looks_like_thread_start` / `is_thread` / `thread_error`.

Build ONE source note from ALL of it — capture is **additive, nothing dropped:**
- `## Summary` and `## Key Points` cover the main tweet **and** the quoted tweet **and** every thread tweet.
- Add a `## Quoted Tweet` section (author + full text) whenever `quote` is present, and a `## Thread` section (the ordered chain) whenever `thread` has more than one entry.
- For EACH url in `video_urls`, run `kb-video-analyze.py` (see the video branch below) and fold its transcript / slide-OCR / figure sections in under a `## Video` heading. The tweets' photos and the video keyframes both become Rule-29 media candidates in Phase 3 / Step A.5.
- The thread is assembled **forward** automatically (works from the **first** tweet — the user can post any tweet in the chain). `flags.is_thread` marks a multi-tweet thread; if `flags.thread_error` is true, only the single focal tweet was retrieved (the `/2/thread` endpoint failed) — note that in the Phase 5 report.
- **Failed/blocked fetch (do NOT blind-ingest):** if `kb-tweet-fetch` returns no usable `main` (error or empty after its built-in retries — e.g. fxtwitter's abuse-block when flooded), treat it as a **failed fetch per Rule 18**: mark the reading-list item for retry and do **not** write a source note. Never ingest a rate-limit/error response as content. In batch (`/kb-queue`), space tweet fetches ~1s apart to avoid tripping the flood block.

**For a source with video (a YouTube/Vimeo watch page, an x.com tweet that has a video, an article with an embedded video, or any `[video]`-tagged item):**

The video analysis is an **ADDITIONAL content layer — it never replaces the source's own text.** First decide which case you're in:

- **Bare video page** (e.g. a YouTube/Vimeo watch URL whose only "text" is title / description / chrome): the video artifact is the content — skip WebFetch.
- **Source that ALSO has a substantive text body** (a tweet's text, a thread, an article wrapped around an embedded video): extract that text the normal way (WebFetch, or the Rule-30 mirror for x.com) **AND** run the video tool, then capture **both in the same note**. The video must never push out an otherwise-relevant text summary.

Run the video analysis tool — it transcribes (captions-first, Whisper fallback), OCRs on-screen slide text, and, with `--vision`, describes figures/charts via the agent CLI; `--summary` adds a DeepSeek text summary:

```bash
set -a; . ~/.config/ai/deepseek.env 2>/dev/null; set +a   # needed only for --summary (DeepSeek text)
"<KB_ROOT>/_tools/.venv/bin/python" "<KB_ROOT>/_tools/kb-video-analyze.py" "<url>" \
  --summary --vision --workdir /tmp/kbva-<slug> --out /tmp/kbva-<slug>/analysis.md --json > /tmp/kbva-<slug>.json
```

- Add `--vision` only when the video likely has figures/charts/diagrams worth interpreting (slide lectures); omit for talking-head/audio-only content to save Claude-plan usage. Tune `--max-frames` for long videos.
- Parse the JSON (or read the `--out` markdown): `metadata`, `transcript`, `summary`, `frames` (each with `ocr_text` + local `path`), and `figures` (Claude descriptions). **Merge this with any text body — do not let the video displace text.** When the source has its own text, `## Summary` and `## Key Points` must cover **both** the text body and the video, and the video gets its own sections (`## Video Transcript`, `## Video — Slide OCR`, `## Video — Figures`). For a bare video page, those video sections plus the artifact summary are the whole note. Either way, nothing relevant from the text body is dropped.
- **Keyframes are already saved locally** by the tool. In Phase 3 / Step A.5, treat only the figure-relevant frames (those whose `figures` description is NOT "no figure") as Rule-29 media candidates and capture them **from their local paths** — their media-description notes' `## OCR` = the frame's `ocr_text` and `## Vision Description` = the figure description (both already produced, so no re-enrichment needed). Skip "no figure" frames (logos/intros/title cards).
- Graceful degradation: the tool exits 0 with partial output if captions/Whisper/x.com/figures partly fail — proceed with whatever it produced and note the gaps. Only a total-failure exit (1) with empty content should stop ingestion.

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

### Step 2.5 — Media (from the Step 2 prep output)

Discovery already ran inside `kb-prep-item.py` — use its `media` array (no bytes downloaded; UI chrome pre-filtered; if the user wants to see what was filtered, re-run `"<KB_ROOT>/_tools/kb-media-capture.py" --discover "<source-url>"` without `--filter chrome`).

**Load media metadocs only if `media` has ≥1 item.** If it is empty (text-only page, dataset-only page, etc.), skip loading `_rules-media.md` and `_schema-media.md` for this source — they are only relevant when there's actual media to handle. If ≥1 item, read both metadocs before proceeding to Phase 3 (Synthesis Review).

Keep the list in memory for Phase 3 (you will present it for approval before any bytes are fetched).

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
> **Source expansion:**
> [Wikipedia cited sources or same-context companion pages discovered, with which ones are proposed now vs. deferred/skipped.]
>
> **Discovered media:**
> [If Step 2.5 returned any items: list each as "- [ ] <url> — <kind>, <caption or alt or 'no caption'>". If none: "No media discovered."]
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
> **Media to capture (Rule 4 approval gate):**
> [If Step 2.5 returned items, list each with a checkbox and the proposed license posture, e.g.
> - [ ] https://example.com/figure-1.png — image, "Forecast graph" — license: quoted-with-attribution
>
> **Contextual relevance filter (applied BEFORE surfacing):** Drop items from the proposal that are not contextually relevant to the article's substantive claims. Specifically default-skip:
> - Book covers (e.g. infobox/sidebar image whose subject *is* the article subject, with no caption — typical of Wikipedia book/album/film entries)
> - Advertisements and sponsor banners
> - Author/byline/profile photos and editor portraits
> - Generic logos (publisher, journal, university wordmarks)
> - Social-media share preview thumbnails
>
> Default-include: scientific figures, charts, diagrams, data tables rendered as images, documentary photographs that illustrate the article's claims, maps, screenshots of primary sources.
>
> Default proposal for remaining items: capture under `quoted-with-attribution`. Skip any the user explicitly declines.
> If nothing remains after the contextual filter: "No contextually-relevant media to capture (N items skipped as covers/ads/portraits/logos)."]
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

### Step A.5 — Capture approved media

For each user-approved media URL from Phase 3, run:

```bash
"<KB_ROOT>/_tools/kb-media-capture.py" --capture <url> [<url> ...] \
  --source-slug "<source-slug-without-date>" \
  --license quoted-with-attribution
```

Pass the source's slug **without** the date prefix — the script prepends today's date automatically. Pass `--from-video <video-url>` for any image that was a video poster (the discovery JSON's `from_video` field).

Parse the captured JSON. For each captured item:

1. Build the `media:` frontmatter entry for the source note (id, kind, status: captured, path, origin_url, captured, mime, bytes, width, height, license, description_note pointing at the new media-description note).
2. Build a `## Captured Media` section for the source note containing one `![[<local_path>|<caption>]]` embed per captured image, followed by a "Description: [[knowledgebase/topics/<topic-slug>/notes/media/<slug>]]" wikilink to its media-description note.
3. Write the media-description note at `topics/<topic>/notes/media/<descriptor-slug>.md`. Use the template from `_media/_README.md` — `author: kb-bot`, `type: media-description`, `media_status: captured`, embed below H1. **For a captured image you MUST always include both a `## OCR` section and a `## Vision Description` section (add `## Transcript` for video/audio); set each to `*Pending.*` when you have not enriched it inline.** Never omit `## OCR` — `kb-ocr-enrich.py` only fills sections that already exist, so a captured image note written without `## OCR` silently skips OCR forever (kb-lint flags this as `media-missing-ocr`). For dedup hits (`dedup_of` set in capture output), do not write a new media-description note; cross-reference the existing one instead per Rule 29 deduplication rule.
4. **Backlink the new media-description note into the wider wiki graph.** Source notes' `media:` frontmatter is frozen at first write per Rule 11, so a media-description note that is only referenced from its parent source frontmatter is graph-orphaned from concept / synthesis navigation. Run `"<KB_ROOT>/_tools/kb-link-media-backlinks.py" --media-note topics/<topic>/notes/media/<descriptor-slug>.md --apply --top 1` to append a `## See Also` entry from the most thematically-relevant mutable concept, entity, or synthesis note (ranked by shared source citations and shared `related:` entries). This is Rule 12 append-only on kb-bot-authored content notes; it does not touch source notes.

If `--capture` returns failures, set the corresponding `media:` frontmatter entry to `status: not-captured` with a `failure_reason` field. Do not lose the URL.

### Step B — Write the source note

Path: `topics/<topic>/sources/<filename>.md`
Filename: `YYYY-MM-DD-<slug>.md`

**Authorship guard:** if this file already exists and lacks `author: kb-bot` frontmatter, abort and report.

The source note must include the `media:` frontmatter block from Step A.5 (if any media was captured) and a `## Captured Media` section between `## Capture Details` (if present) and `## Summary`.

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

<Points enriched by the conversation — not just what the source says, but what the user found interesting or worth connecting to existing knowledge. Link to existing KB notes where relevant: [[knowledgebase/topics/<topic-slug>/notes/concepts/concept-slug]]>

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
sources: ["[[knowledgebase/topics/<topic-slug>/sources/<source-filename>]]"]
related: []
---

# <Concept Name>

<One sentence: what this concept is and why it matters.>

*This is a stub. A full article will be written once this concept appears in two or more sources.*

**Source:** [[knowledgebase/topics/<topic-slug>/sources/<source-filename>]]
```

Update `_checkpoint.md` after each stub.

### Step D — Update the reading list

If `topics/<topic>/reading-list.md` exists:
- Mark the current source processed (locked) via `"<KB_ROOT>/_tools/kb-checkoff.py" --file topics/<topic>/reading-list.md --url "<url>" --mark x --note "processed <date> → [[knowledgebase/topics/<topic>/sources/<slug>]]"`.
- Append approved follow-up **URL** sources as new unchecked items under the `## Pending` section of `reading-list.md` (canonical section ladder defined in `_schema.md`: only Pending/Approved/Auto-approved/Processed/Failed — Retry are permitted level-2 headings); put prose-only rabbit holes in `topics/<topic>/research-ideas.md`. Never write `_dive-queue.md` (generated/read-only, Rule 13).
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
- Created: [any media-description notes, one per line]
- Captured media: [origin URL → media_id → local_path, one per line]
- Updated: topics/<topic>/reading-list.md
- Updated: topics/<topic>/_summary.md
- Updated: _index.md
```

### Step H — Refresh the frontmatter cache

```bash
kb-index --update
```

This keeps `kb-search` accurate for the next session. If `kb-index` is not on PATH, skip silently.

### Step I — Complete the checkpoint

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
- Internal cross-references use wikilinks: `[[knowledgebase/topics/<topic-slug>/notes/concepts/slug]]`
- **Never write `[[https://...]]`** — Obsidian treats this as a note filename and creates a broken link
