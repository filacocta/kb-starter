---
author: kb-bot
created: 2026-10-25
modified: 2026-10-25
type: meta
title: KB maintenance
---

# KB maintenance

Read the [write contract](../_rules.md). Structural audit and ordinary lookup are
read-only. Run `kb-audit` for active inventory, exact duplicates and topic coverage;
use `_tools/kb-lint.py --kb-root ROOT --topic SLUG` or another documented selector
for the affected Personal records. Semantic review is a separate evidence task.

Use a task-sized scope. Do not require inbox, clippings, prospects or every
project summary unless the requested maintenance needs them. Keep original capture
text and provenance. Capture routing does not establish relevance, approval or
ingestion. Explicit trusted feed lanes retain their configured authorization;
ordinary capture stays Pending. Unmapped inputs do not silently select a domain.

Queue and ledger mutations use their lock-safe tools. Generated dashboards are
views. Update `_index.md` for actual navigation changes and `_writing-prompts.md`
only when maintaining that view is part of the task; avoid copying the same prompt
to multiple stores. Prefer links or generated views over synchronized prose.

Mutable article corrections may replace obsolete wording with cited evidence.
Keep useful decision history and disagreement. Published source corrections
use `kb-source-successor.py` with exact manifest/review, bounded lifecycle metadata
or `kb-repair-source-wikilinks.py` for navigation-only defects. A same-URL new
version is not an alias. Path moves must preserve source content, receipts and
inbound links, and need a concrete reviewed migration rather than routine cleanup.
A byte/path repair can invalidate old completion bindings. Keep old receipts as
historical observations; obtain fresh validation and review before new completion.
Never rewrite a receipt to claim it reviewed different bytes.

Exact-byte duplicates establish identity, not the canonical owner. Search may
collapse them with all equivalent paths. Prefer leaving valid small historical
copies over a migration with no measured benefit. A canonical decision binds
the inspected paths and hashes and becomes stale if they change.

Published sources, original books/media and human content are not disposable.
Before authorized deletion of generated intermediates, prove the exact files,
unchanged hashes, completed consumption and retained rebuild inputs. Preserve
failure/interruption evidence until dispositioned. Invalid agent records need
replacement provenance and repaired inbound citations before corrective removal.
No broad directory purge or silent rewriting of source history.

Legacy metadata is reported separately from new violations. Do not invent
owners, dates, deployment status or missing evidence to satisfy a lint.
Run changed-file checks, verify source/receipt preservation and summarize any
remaining uncertainty. The release's contract manifest detects copy drift;
an audit result cannot certify that all corpus claims are true.
