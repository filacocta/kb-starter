---
name: kb-wrap
description: Update the KB at a task stopping point. Updates the relevant project _summary.md (status checkboxes, decisions, open questions), adds topic notes if new knowledge was surfaced, and appends a summary entry to _log.md. Run when a feature, investigation, or session has reached a natural stopping point.
user-invocable: true
---

You are updating the KB to reflect the work just completed.

## KB Location

KB root: `/path/to/your/vault/knowledgebase/`

**Context:** $ARGUMENTS

If $ARGUMENTS is empty, ask the user: "What project or topic should I update, and what was completed?"

---

## Procedure

### 0 — Lint and fix gate

Before writing any KB files, run:

```bash
kb-lint --no-orphans
kb-fix --fix all
```

Show the `kb-lint` summary to the user. If `kb-fix` applied changes, note what was fixed. Then proceed.

If `kb-lint` reports unfixable issues (broken wikilinks, orphans, stale dates), surface them briefly — don't skip past silently. They don't block the wrap but the user should know.

### 1 — Identify scope

From $ARGUMENTS (or user response), determine:
- Which **project** was worked on (if any) → `projects/<slug>/_summary.md`
- Which **topics** had new knowledge surface (if any) → `topics/<slug>/_summary.md` or a new note

### 2 — Update project summary (if applicable)

Read the current `projects/<slug>/_summary.md`. Apply:

- **Check off completed items** in `### Done` / `### Open / TODO` — move newly done items from Open to Done, mark `[x]`.
- **Add newly discovered open items** to `### Open / TODO`.
- **Record architectural decisions** — if any design choices were made during the work that aren't otherwise captured, add a `## Decisions` section (or append to an existing one).
- **Resolve or update open questions** — if any of the listed design questions were answered, note the answer inline.
- Update `modified:` in frontmatter to today's date.

Do not rewrite prose that doesn't need changing. Surgical edits only.

### 3 — Update topic notes (if applicable)

If the work surfaced a new technique, pattern, or tool behaviour worth preserving:
- Check whether an existing concept note covers it. If so, add a sentence or short section.
- If no note exists and the knowledge is substantial, create one at `topics/<slug>/notes/concepts/<slug>.md` following the KB schema.
- Update the topic `_summary.md` Key Concepts list if a new note was added.

Skip this step if no new general knowledge was produced (e.g., pure bug fixing with no transferable insight).

### 4 — Append to _log.md

Read the current tail of `_log.md` to match the existing format, then append:

```
## <Today's date>

**Project/Topic:** <name>
**Operation:** <one line: what was done>
**Key decisions / outcomes:**
- <bullet per decision or notable outcome>
**Status:** <where things stand now>
```

### 5 — Refresh the frontmatter cache

After all writes are complete, run:

```bash
kb-index --update
```

This keeps `kb-search` accurate for the next session. If `kb-index` is not on PATH, skip silently — it means the tool isn't installed yet.

---

## Authorship Guard

Only write to files that have `author: kb-bot` in frontmatter, or to `_log.md` and `_index.md` (KB infrastructure). Never modify user-authored notes.

## What not to do

- Do not update the KB mid-task. This skill is for stopping points only.
- Do not pad entries. One terse bullet per decision is correct; a paragraph is too much.
- Do not create notes for knowledge that is already captured or that can be derived from the codebase.
