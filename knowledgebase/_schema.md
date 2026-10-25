---
author: kb-bot
created: 2026-04-09
modified: 2026-10-25
type: meta
title: Personal KB formats
---

# Personal KB formats

This file describes records. [_rules.md](_rules.md) owns shared write constraints;
the selected [_workflows](AGENTS.md) page owns procedure. Optional fields are not
blanket migration requirements for historical notes.

## Locations

| Path | Record |
|---|---|
| `topics/<slug>/_summary.md` | Compact topic orientation and links. |
| `topics/<slug>/sources/` | Inspected source versions. |
| `topics/<slug>/notes/` | Maintained concepts, entities, syntheses and media descriptions. |
| `topics/<slug>/reading-list.md` | Source approval and completion rows. |
| `topics/<slug>/research-ideas.md`, `_prospects.md` | Research questions and possible projects. |
| `projects/<slug>/_summary.md`, `_todos.md`, `notes/` | Project orientation, tasks and durable decisions/handoffs. |
| `readinglist/Links.md`, `readinglist/topics.md`, `ideas/_backlog.md` | Unassigned captures and broad ideas. |
| `_library/`, `_media/` | Original documents and captured media. |
| `_tools/` | Executable helpers, operation receipts and staging. |
| `_index.md` | Human navigation, updated when topology changes. |
| `_log.md` | Append-only mutation history, not query telemetry. |
| `_dive-queue.md` | Generated view, never approval authority. |

Slugs are lowercase hyphenated names. New source names use a date and descriptive
slug, or an academic author/year/title. Existing paths remain stable.

## Markdown metadata

New agent notes carry `author: kb-bot`, `created`, `modified`, `type`, `topic`,
`title`, `description`, `tags`, `sources` and `related` as applicable.
Dates are YYYY-MM-DD. The title identifies the H1. `modified` dates document
editing, not verification. Lists contain real links or tags, not invented IDs.

Common types are `source`, `concept`, `entity`, `synthesis`, `output`, `summary`,
`index`, `meta`, `todos`, `prospects`, `research-ideas`, `catalog` and
`media-description`. Existing descriptive project roles remain valid.
Article states use `draft`, `review`, `final`, `deprecated`; source retirement
uses `status: deprecated` and `superseded_by` when a successor exists.

### Source records

A source has one canonical `origin` URL or supported local locator. `mirror`
records a different transport endpoint without changing identity.
`ingest_status: failed` describes an unsuccessful historical attempt and cannot
satisfy source completion. New successful sources have `## Source-scoped Record`
and `## Evidence Boundary`. Dense material also has `## Content Map`.
Its scope, omissions and limitations describe what was actually inspected.
`media` uses [_schema-media.md](_schema-media.md) only when applicable.

### Queues

Topic reading lists have these level-2 sections in this order:

| Section | Meaning |
|---|---|
| `Pending` | Capture or triage lead, not source approval. |
| `Approved` | Interactive ingestion authorized. |
| `Auto-approved` | Explicit unattended ingestion authorization. |
| `Processed` | Historical terminal archive. Current completion normally marks the original row in place. |
| `Failed — Retry` | Failed work awaiting a scoped retry. |

A real row starts `- [ ] <primary locator>` outside YAML, fences, comments,
blockquotes and indented code. Subgroups use level-3 headings. One independent
locator maps to one source version. An annotation URL is not the row's locator.
`kb-queue-parse.py` defines the supported Markdown subset and rejects ambiguous
approval sections. Prose-only questions belong in research-ideas, not a source lane.

Completion annotations bind the source path/hash and review-receipt hash.
Existing completed rows without receipts remain historical, not retroactively
certified. Global inbox routing is not source publication.
The deployed unattended selector accepts unchecked HTTP(S) URL rows only.
Local PDFs require a supported interactive queue path; they are not scheduled
merely because the generic row parser can recognize them.

### Project records

Summaries and task boards are maintained views. A short summary links detailed
history instead of copying it. Aim below 300 body words; the linter reports
longer summaries without truncating them. `_todos.md` uses Backlog, In Progress,
Testing/Review and Done; existing emoji/spacing variants remain valid.
Markers are `[ ]` planned, `[~]` in progress, `[?]` review, `[x]` done and `[!]` blocked.
They record state, not authorization.

An optional `context_role: current | decision | session` distinguishes maintained
views from dated records. Code projects may use `**Code location:**` with an exact
local repository path in backticks. Git observation metadata is optional as a
whole: `code_state_repo_id`, full `code_state_commit`, boolean `code_state_dirty`,
timezone-bearing `code_state_observed_at` and
`code_state_scope: repository-git-metadata`. Incomplete records are unknown,
not verified. Local identities are personal metadata, not team-KB content.

## Links

Personal wikilinks start at its containing vault, for example
`[[knowledgebase/topics/earthquake-prediction/_summary]]`. Use Markdown links
for external URLs. Do not put URLs or `../` in wikilinks. Work-vault links use
that repository's different root. Outbound personal-vault references are read-only
and may be listed in `vault_refs`; they grant no access to unrelated content.

## Tooling formats

JSON evidence, review and successor artifacts carry `author: kb-bot` and their
tool's versioned schema. Their creating/verifying helpers own exact fields.
They are operation evidence, not topic citations. Raw binaries and extraction
payloads in `_library` do not carry YAML; Markdown control records do.

`_url-aliases.json`, when present, is version 1 with `author`, `version`, `entries`.
Each entry has exact `alias`, `source_note`, `evidence`, `verified` fields.
It identifies a verified changed address, not a document revision or inferred
equivalence. Invalid aliases or origin collisions stop identity matching.
Host controller state follows its configured runbook and `_tools/deploy/README.md` when present. Shared templates do not activate a scheduler.
