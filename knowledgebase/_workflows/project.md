---
author: kb-bot
created: 2026-10-25
modified: 2026-10-25
type: meta
title: Project context
---

# Project context

Read the [write contract](../_rules.md) before writes. Context retrieval itself
is read-only. For topic-based project requests, retrieve the relevant topic
and search existing projects before initializing another one. Use code and the
prompt for facts already available there.

A Personal project normally has a short `_summary.md`, `_todos.md` and only
useful decision/context notes. If a repository or external board is already
authoritative, link it and keep the KB page as orientation, not a duplicate queue.
Reuse the existing templates where helpful; no mandatory catalog at five items
or folder migration at ten files. Create folders when navigation benefits.

Keep Observed, Planned and Externally verified evidence separate. Code claims
need a named revision/path and actual inspection; tests need the command and
result; deployment needs a named environment and observation time. A recent
document edit or matching Git commit does not prove implementation or live state.
Use `kb-project-status.py --kb-root ROOT SLUG` only for the metadata it reports;
runtime checks are additional task scope, never a startup requirement.

For init, create the named project's orientation, realistic acceptance criteria
and tasks, then add its navigation link. A request to create it authorizes
ordinary KB filing. It does not authorize deployed services or unrelated work.
For wrap, save decisions, costly-to-recover rationale, checks, blockers and the
next step. Do not transcribe the session or copy code. Link the actual execution
handoff wherever legitimately owned. Historical handoffs cannot resume work.

Update current views surgically. Preserve dated decisions and prior observations
in place or in linked history before compressing them. Task completion needs
actual evidence; held projects should not display historical markers as active
work. Preserve human annotations and never auto-erase completed tasks.

Run checks only for affected records and record the mutation once. Query mode
does not triage inboxes or write logs. A small authorized edit needs no new global
checkpoint. Shared/team KBs use their own governance, not Personal authorship,
feed or source-publication machinery.
