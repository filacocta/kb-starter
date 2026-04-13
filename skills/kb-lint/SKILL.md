---
name: kb-lint
description: Health check for a KB topic (or all topics). Checks for broken wikilinks, orphan pages, missing stubs, incomplete frontmatter, contradictions flagged with ⚠️, and stale sources. Produces a lint report filed to notes/syntheses/ and offers to create any missing stubs found. Run after /kb-synthesise to catch coverage gaps and structural problems.
user-invocable: true
---

You are acting as a knowledge base health-check agent. Your job is to audit one or more topics in the KB for structural problems — broken links, orphan pages, missing stubs, incomplete frontmatter, flagged contradictions — and produce an actionable lint report.

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

## Startup

1. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, surface it and offer to resume.
2. Read `_schema.md` and `_index.md`.

**Argument:** $ARGUMENTS

- If a topic slug was given (e.g. `cellular-automata`), audit that topic only.
- If `all` was given, audit every topic listed in `_index.md`.
- If no argument: "Which topic should I lint? (provide a topic slug, or 'all' for all topics)"

---

## Phase 1 — Build the File Map

For the target topic(s), enumerate all note files and build two structures:

**File map** — every `.md` file that exists under `topics/<topic>/notes/` and `topics/<topic>/sources/`, mapped to its path relative to `knowledgebase/`:
```
notes/concepts/genetic-algorithm.md → exists
notes/entities/deap.md → exists
...
```

**Wikilink inventory** — scan every file in scope for all wikilinks in the forms `[[some/path]]`, `[[some/path|display]]`, and `[[sources/slug]]`. For each wikilink, record:
- Which file it appears in (the referrer)
- The target path it resolves to (relative to the topic folder or KB root)

To scan for wikilinks efficiently:
```bash
grep -rn "\[\[" topics/<topic>/notes/ topics/<topic>/sources/
```

---

## Phase 2 — Run the Seven Checks

Run all checks in order. Collect findings; do not stop at first error.

---

### Check 1: Broken Wikilinks

A wikilink is broken if its target path does not correspond to any existing file. For each wikilink found in Phase 1:

1. Resolve the path: if the wikilink is `[[notes/concepts/foo]]`, check whether `topics/<topic>/notes/concepts/foo.md` exists.
2. If not, record it as broken.

Use `grep` + file existence checks (via Glob or Bash `test -f`) to do this efficiently — do not read every file.

**Output format:**
```
### ❌ Broken Wikilinks
| File | Broken link | Notes |
|---|---|---|
| notes/concepts/foo.md:12 | [[notes/concepts/bar]] | bar.md does not exist |
```

---

### Check 2: Orphan Pages

A note is an **orphan** if no other note in scope links to it. For each existing note file, check whether it appears as a target in any wikilink from the inventory.

Exclude from orphan check:
- `_summary.md`, `_discussion.md`, `reading-list.md` (these are meta files, not wiki articles)
- Files in `sources/` (source notes are cited by wikilink in the `sources:` frontmatter field, which may not be a body wikilink)

**Output format:**
```
### ⚠️ Orphan Pages
| File | Last modified |
|---|---|
| notes/concepts/orphaned-concept.md | 2026-04-10 |
```

---

### Check 3: Missing Stubs

Scan the body text of all notes (not just frontmatter) for references to concepts that do not yet have a note. Look for:
- Proper-noun concept names in italics or bold that recur across multiple notes
- `[[notes/concepts/slug]]` links that are broken (already caught in Check 1, but also note which broken links appear 3+ times — these are high-priority stubs)
- Concepts mentioned in `related:` frontmatter fields that do not exist

For each missing concept appearing 3 or more times across notes, propose creating a stub. Rank by frequency.

**Output format:**
```
### 🔧 Missing Stubs (top candidates)
| Concept | Mentions | Would create |
|---|---|---|
| Schelling Segregation | 4 | notes/concepts/schelling-segregation.md |
```

---

### Check 4: Incomplete Frontmatter

For each note file, read its frontmatter and verify all required fields are present and non-empty. Required fields (from `_schema.md`):
- `author` — must be `kb-bot`
- `created` — must be a date
- `modified` — must be a date
- `type` — must be one of `source`, `concept`, `entity`, `synthesis`, `summary`, `index`, `meta`
- `topic` — required for all types except `meta` and `index`
- `title` — required for `concept`, `entity`, `synthesis`, `source`
- `description` — required for `concept`, `entity`, `synthesis`, `source`
- `status` — required for `concept`, `entity`, `synthesis` (not for `source`, `meta`, `index`)

**Output format:**
```
### 📋 Incomplete Frontmatter
| File | Missing fields |
|---|---|
| notes/concepts/foo.md | description, status |
```

---

### Check 5: Contradictions

Search all note bodies for the `⚠️` character, which marks explicitly flagged contradictions. List each occurrence with file and line number so the user can review them.

Also: scan for any concept note where the `description:` frontmatter field contradicts what is written in the body (e.g. the description says a concept is a stub but the body has a full article). Flag these as description mismatches.

**Output format:**
```
### ⚠️ Flagged Contradictions
| File | Line | Excerpt |
|---|---|---|
| notes/concepts/foo.md:34 | ⚠️ Contradiction: source-a says X; source-b says Y |
```

---

### Check 6: Stale Sources

Read the `created:` field from each source note in `topics/<topic>/sources/`. If the source was ingested more than 6 months before today's date, and no related concept/synthesis note has been `modified:` within the last 6 months, flag it as potentially stale.

Note: stale does not mean wrong — it means "this source's contribution may not have been fully propagated into the wiki, or may need revisiting for new developments." Flag for human review, do not auto-update.

**Output format:**
```
### 🕐 Stale Sources (>6 months, no recent derivative note)
| Source | Ingested | Last related note modified |
|---|---|---|
| sources/2025-09-01-foo.md | 2025-09-01 | 2025-09-01 |
```

---

### Check 7: Suggested Research Questions

Based on the gaps revealed by Checks 1–6 and the state of the `## Gaps & Open Questions` section in the topic's `_summary.md`, generate 3–5 specific research questions that would fill the most important remaining holes. Frame these as questions that could be answered by ingesting a specific source.

**Output format:**
```
### 🔬 Suggested Research Questions
1. How does White & Engelen's 113-cell neighbourhood perform vs smaller kernels in calibration studies? (→ ingest White & Engelen 1993)
2. What are the full formal properties of asynchronous CA compared to synchronous? (→ ingest Bak-Tang-Wiesenfeld 1987)
```

---

## Phase 3 — Present Summary

Before writing the report, present a brief summary to the user:

> ## Lint results for `<topic>`
>
> | Check | Status | Count |
> |---|---|---|
> | Broken wikilinks | ❌ / ✅ | N |
> | Orphan pages | ⚠️ / ✅ | N |
> | Missing stubs | 🔧 / ✅ | N |
> | Incomplete frontmatter | 📋 / ✅ | N |
> | Contradictions flagged | ⚠️ / ✅ | N |
> | Stale sources | 🕐 / ✅ | N |
>
> **Top issues:**
> - [brief description of most critical finding]
> - [second most critical]
>
> Write the full lint report to `notes/syntheses/lint-report-<YYYY-MM-DD>.md`? (yes / no)
> Create missing stubs automatically? (yes / no / show me the list first)

Wait for the user's response before writing anything.

---

## Phase 4 — Write the Lint Report

If the user approves, write the lint report:

**Path:** `topics/<topic>/notes/syntheses/lint-report-<YYYY-MM-DD>.md`

**Authorship guard:** check that the directory exists and that no file of this name already exists (date collision is unlikely but possible). If a report for today already exists, append `-2` to the filename.

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: synthesis
topic: <topic-slug>
title: "Lint Report — <Topic> — <YYYY-MM-DD>"
description: "Health check report: broken wikilinks, orphan pages, missing stubs, frontmatter issues, contradictions, stale sources, and suggested research questions."
status: draft
tags: ["lint", "meta", "<topic>"]
sources: []
related: []
---

# Lint Report — <Topic> — <YYYY-MM-DD>

**Generated:** <YYYY-MM-DD>
**Topic:** <topic-slug>
**Files scanned:** N notes, M sources

---

## Summary

| Check | Status | Count |
|---|---|---|
| Broken wikilinks | ❌/✅ | N |
| Orphan pages | ⚠️/✅ | N |
| Missing stubs | 🔧/✅ | N |
| Incomplete frontmatter | 📋/✅ | N |
| Contradictions flagged | ⚠️/✅ | N |
| Stale sources | 🕐/✅ | N |

---

[Full output from each check]

---

## Action Items

Summarise the checks as a prioritised to-do list:
- [ ] Fix broken wikilink in `notes/concepts/foo.md` → create `notes/concepts/bar.md` or fix the link
- [ ] Create stub for Schelling Segregation (4 mentions, no note)
- [ ] Review contradiction in `notes/concepts/foo.md:34`
- [ ] Add missing `description` field to `notes/concepts/baz.md`
```

---

## Phase 5 — Auto-create Missing Stubs

If the user approved stub creation:

For each stub in the "Missing Stubs" list that the user approved, create it using the standard stub format (see `/kb-synthesise` skill for the template). Update `_summary.md` note count and `_index.md` accordingly.

Apply the authorship guard before every write.

After creating stubs, append to `_log.md`:
```markdown
## [YYYY-MM-DD] lint | <topic>

- Lint report filed: topics/<topic>/notes/syntheses/lint-report-<date>.md
- Stubs created: [list or "none"]
- Issues found: N broken links, N orphans, N missing stubs, N frontmatter issues, N contradictions, N stale sources
```

---

## Phase 6 — Report

> **Lint complete for `<topic>`**
>
> **Report:** `obsidian://open?vault=<VAULT_NAME>&file=knowledgebase%2Ftopics%2F<topic>%2Fnotes%2Fsyntheses%2Flint-report-<date>.md`
>
> **Issues requiring action:**
> - [list of action items, or "none — topic is clean"]
>
> **Next steps:**
> - Fix broken wikilinks by creating the missing note or correcting the link
> - Run `/kb-queue <topic>` to process queued sources that would fill the missing stubs
> - Run `/kb-synthesise <topic>` again after adding new sources

---

## Authorship Guard (always active)

Before every write:
1. Target path must start with `knowledgebase/`
2. If file exists, read it first and verify `author: kb-bot` in frontmatter
3. Hard abort on any violation — report and stop

## Important Notes

- **Read-only by default.** All seven checks are read operations. No files are written until Phase 4, and only with user approval.
- **Lint report is a synthesis note.** It lives in `notes/syntheses/` and counts toward the note total. Update `_summary.md` accordingly.
- **Do not auto-fix broken wikilinks.** Only stubs can be auto-created. Broken link targets may need the user to decide whether to create the note or correct the link.
- **Contradiction detection is limited.** The agent can reliably find explicit `⚠️` flags and description-vs-body mismatches. Deep semantic contradiction detection across many notes is not reliable and should not be attempted — flag what is explicitly marked, and note the limitation.
