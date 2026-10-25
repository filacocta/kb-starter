---
author: kb-bot
created: 2026-10-25
modified: 2026-10-25
type: meta
title: Source ingestion
---

# Source ingestion

Read the short [write contract](../_rules.md) and the selected topic summary.
Consult [formats](../_schema.md) or [tool endpoints](../_tools/README.md) for fields
or command syntax, not as compulsory startup reading.

## Scope and preflight

Distinguish inspection from ingestion. A request to ingest named sources covers
their normal filing and completion; do not re-request approval at each stage.
Keep the finite target list and media scope. New companions, searches, acquisitions
or account/billing changes require task scope that covers them. A request for
research may cover ordinary searches without a URL-by-URL approval menu.

For each URL run `kb-prep-item.py --kb-root ROOT --url URL --skip-media` before
fetching. Identity lookup errors stop matching; a seen source is not proof of
valid publication. Validate and review it before a new checkoff.
Use canonical origins and documented transport mirrors. Keep inspected revisions
distinct from address aliases. Fetch once within a finite byte/time budget;
record access/coverage limitations rather than escalating automatically.

Before publication, ensure one exact row in the topic's Approved lane using
`kb-capture-url.py` and `kb-promote.py`. Verify the destination, not a fallback
global inbox. A batch records its target rows before fetching. Local, pasted
or clipping material with no supported queue-add/publication path stops before
publication; preserve the input, never invent a URL or hand-edit an approval row.

## Draft, review and publish

Preserve source argument, methods, conditions, relevant results and omissions.
Keep social/marketing claims attributed. Do not add KB conclusions to immutable
source prose. Use the required source sections in the schema. Retain only
permitted extraction evidence with `kb-retain-evidence.py`; its hash establishes
identity, not truth or complete coverage.

Stage the draft outside its final path. Validate its intended destination with
`kb-validate-source-candidate.py --kb-root ROOT --staged-text DRAFT --candidate-path FINAL`.
Errors block publication. Warning acceptance needs a source-specific reason.
An independent reviewer compares the exact draft to the actual inspected evidence,
records coverage and omissions and creates a `kb-review-receipt.py` artifact.
Different writer/reviewer references describe separate passes; they do not
authenticate identities. Changed draft bytes require a new receipt.
If evidence cannot be retained, record non-reproducibility and retention limits;
do not retain restricted content merely to satisfy a check.

Publish only with `kb-commit-source.py` using the exact draft, destination,
queue, primary locator and canonical review receipt. `SOURCE_AND_QUEUE_COMPLETE`
covers those two artifacts. `SOURCE_WRITTEN_QUEUE_PENDING` requires inspection
and reconciliation using the same bytes, not a source overwrite or assumed rollback.
Update mutable navigation only where its content changed; append one operation
receipt. Record whether article maintenance is incorporated, no change or deferred.
Do not fabricate synthesis completion from a checked queue row.

## Conditional paths

- Media capture/inspection uses [media](media.md), including its separate retention scope.
- Citation indexes such as Wikipedia normally discover references rather than
  become sources of record. Publish the index itself only when requested.
  Resolve/fetch references only within scope; deduplicate and cap expansion,
  normally about 20 useful candidates, leaving the rest as a bounded lead.
- Use existing authenticated access or supplied files for gated material.
  Bounded archive/browser recovery is optional within scope, not an obligatory
  bypass/retry ladder. Preserve original origin and the actually inspected
  archive date/URL. Transient failures remain retryable; do not declare a host
  permanently dead merely because a mirror failed.
- Defensive security material records threat models and mitigations. Exact
  payloads belong only in scoped authorized evaluation fixtures, not generic
  operational instructions or unreviewed tool installations.
- Books and long-form custody use [library](library.md).

## Unattended worker

The controller supplies exactly one still-unchecked Auto-approved HTTP(S) row.
Recheck it at publication. Use its operation-scoped checkpoint and configured
writer/reviewer route. No new work selection, reference searches, interactive
browser recovery, temporary media inspection, provider fallback or latch reset.
Default to deferring media/citation-only cases unless the deployment explicitly
supports them. Follow the controller's exact result schema and navigation
receipt requirements. Failure returns control with evidence and no unchecked
completion claim; existing service configuration is not changed by this flow.
