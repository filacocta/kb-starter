---
name: kb-scan
description: Light KB scan for the current task. Uses kb-search to find relevant notes by keyword/tag/topic without reading full files, then selectively loads 1-3 high-value notes. Replaces the old _index.md → _summary.md ladder.
user-invocable: true
---

You are performing a focused KB scan to orient yourself before starting work.

## Configuration

Read `~/.claude/kb-config.md` to get KB_ROOT.

**Task / topic:** $ARGUMENTS

---

## Scan Procedure

### Step 1 — Search by keyword (primary)

Run `kb-search` with keywords from the task description:

```bash
kb-search --query "$ARGUMENTS" --limit 10
```

If `$ARGUMENTS` contains a recognizable KB topic slug (e.g. "cellular-automata", "weatherstone"), also run a topic filter:

```bash
kb-search --topic "<slug>" --limit 10
```

`kb-search` reads only the frontmatter cache (`_kb-index.json`) — it does NOT open note files. The compact output (title + tags + description) is enough to judge relevance without loading full content.

### Step 2 — Selective deep-read (1-3 files max)

From the results, pick the 1-3 notes with the highest relevance. Read ONLY those full files. Prefer:
- `_summary.md` of a matching topic or project (for broad context)
- A specific `notes/impl/plan-*.md` or `notes/concepts/*.md` that directly addresses the task
- Skip notes you already have in context this session

### Step 3 — Check project impl-plans (if project-relevant)

If the task relates to a project, run:

```bash
kb-search --topic "<project-slug>" --type impl-plan --limit 5
```

Read the most relevant impl-plan if one exists.

---

## Output Format

Produce a terse oriented briefing. Do not pad or narrate the scan. Format:

**Relevant topics:** [comma list, or "none"]

**Relevant projects:** [comma list, or "none"]

Then for each match, a short block:

> **[Topic or Project name]**
> - Key facts or constraints directly applicable to the task
> - Open questions or prior decisions that matter
> - Any notes worth reading (title + path)

**From the research queue:** only check `_dive-queue.md` for exploratory research tasks, not implementation.

If nothing in the KB is relevant, say so plainly: "No KB entries relevant to this task."

---

## Token cost vs old approach

Old approach: `_index.md` (2 KB) + 2-3 `_summary.md` files (3-5 KB each) = ~15 KB minimum.
New approach: `kb-search` output (~2 KB) + 1-3 targeted file reads = ~7 KB typical.
Savings: ~50% token reduction on KB navigation overhead.

---

## Authorship Guard

Do not write to any file during this scan. This skill is read-only.
