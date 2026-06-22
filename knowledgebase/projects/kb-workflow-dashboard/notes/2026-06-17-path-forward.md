---
author: kb-bot
created: 2026-06-17
modified: 2026-06-17
type: output
topic: kb-workflow-dashboard
title: "KB Workflow Dashboard — Path Forward"
description: "Starter-safe direction for evolving the workflow dashboard from a read-only status view into a guarded operational surface."
status: draft
tags: ["dashboard", "workflow", "safe-operations", "command-runner", "skills"]
sources: []
related:
  - "[[knowledgebase/projects/kb-workflow-dashboard/_summary]]"
  - "[[knowledgebase/projects/kb-workflow-dashboard/_todos]]"
---

# KB Workflow Dashboard — Path Forward

The dashboard should stay a live operational surface for the KB, not a second source of truth. It should read the configured KB root, surface what needs attention, and eventually perform narrow actions through the same shared tools that agents use.

## Code Placement

The starter app lives at `projects/kb-workflow-dashboard` in this repository. A local installation can copy or run that app beside a real vault while pointing `--kb-root` at the live `knowledgebase/` folder. Durable project notes live under `knowledgebase/projects/kb-workflow-dashboard`.

Keep the starter scaffold sanitized: no personal paths, private repository names, account names, credentials, machine-specific assumptions, or live vault data snapshots.

## Safe Operations

The first interactive actions should be a named allowlist, not arbitrary shell execution:

- Run KB lint with confirmation.
- Regenerate the generated dive queue.
- Check off a queue/backlog item through `_tools/kb-checkoff.py`.
- Copy/open paths.
- Start or stop the local dashboard server only after showing port and PID ownership.

Each action should show the exact command, working directory, target file(s), expected write class, and verification step before it runs.

## Skill And Agent Handoffs

Agent skills are not pure API calls. The first dashboard support for them should generate copyable handoff prompts that include the selected queue item, target topic/project, and relevant KB paths. Whitelisted CLI launches require a later design pass with checkpoint enforcement, visible prompt/transcript, cancel behavior, dirty-state checks, and post-run lint.

Repeated agent behavior should be promoted into deterministic `_tools/` scripts when possible. The dashboard should prefer calling those tools over launching an agent.

## External Skill Artifacts

Cloneable Gists/repos that claim to be skills should be reviewed before installation or starter inclusion. The starter-safe flow is: keep an original scratch checkout, create a normalized package copy, run the skill validator, compare the artifact against KB synthesis, record provenance/scope/test gaps, then choose install, adapt, extract ideas only, or leave as reference.

The dashboard should eventually display that state as a workflow lane. It should not clone repositories or install skills automatically until the guarded command-runner and approval model are designed.

## Long-Form Sources And Skill Candidates

Books, reports, long articles, and article-length PDFs often cross topic boundaries. The dashboard should eventually support a cataloged custody model: raw files stay outside topic folders, while topic notes capture only the sections actually ingested for a topic.

A future lane should show:

- available raw source
- catalog metadata
- triaged chapters/sections
- extracted topic-relevant material
- synthesized topic or source-cluster notes
- candidate skill or explicit context-only decision
- eval fixture and validation status
- install/adapt/starter/include/ignore decision

This lane should pair with the starter-safe `book-to-skill` meta-skill. The dashboard can show status and generate handoff prompts before it can safely run any skill or install any candidate.

## Open Design Questions

- Where should command-runner logs live: local runtime state, KB project notes, or both?
- Should lint status be cached instead of run on every refresh?
- Which Phase 2 actions are safe enough for the first command runner?
- Should the dashboard stay pinned to one KB root or support multiple configured roots?
- How should external skill artifacts be reviewed before installation?
- Should long-form source custody use `books/`, `library/`, or another cataloged path outside topic folders?
- What minimum candidate-skill registry is useful before the dashboard can run install actions?
