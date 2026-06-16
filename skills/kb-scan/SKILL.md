---
name: kb-scan
description: Light KB scan for the current task. Reads _index.md, identifies relevant topics and projects, reads their _summary.md files, and surfaces anything directly applicable — prior decisions, design constraints, open questions. Run this at the start of a work session on a named topic or project.
user-invocable: true
---

You are performing a focused KB scan to orient yourself before starting work.

## KB Location

At startup, read `~/.claude/kb-config.md` to get `KB_ROOT` (the KB root path) and `VAULT_NAME`. If that file does not exist, stop and tell the user to create it — see the kb-starter README.

**Task / topic:** $ARGUMENTS

---

## Scan Procedure

1. Read `_index.md`.
2. Identify all topics and projects in the index that are semantically relevant to the task or topic in $ARGUMENTS. Cast a wide net — include adjacent topics if there is plausible overlap.
3. For each relevant match, read its `_summary.md`.
4. Read `_dive-queue.md` briefly — flag any queued research items that intersect the task. (It is a generated dashboard, regenerated on every queue check-off and nightly kb-auto run.)

*Optional accelerator:* if the `obsidian` CLI is available (requires the desktop app to be running), `obsidian search "<term>"` can locate candidate notes faster than grep when the index/summaries don't make the target obvious. Never treat CLI availability as a dependency — fall back to Grep.

---

## Output Format

Produce a terse oriented briefing. Do not pad or narrate the scan process itself. Format:

**Relevant topics:** [comma list, or "none"]

**Relevant projects:** [comma list, or "none"]

Then for each match, a short block:

> **[Topic or Project name]**
> - Key facts or constraints directly applicable to the task
> - Open questions or prior decisions that matter
> - Any notes or sources worth reading before proceeding (title + vault path)

**From the research queue:** [any directly relevant queued items, or "none"]

If nothing in the KB is relevant, say so plainly: "No KB entries relevant to this task."

---

## Authorship Guard

Do not write to any file during this scan. This skill is read-only.
