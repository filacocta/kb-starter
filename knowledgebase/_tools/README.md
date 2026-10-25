---
author: kb-bot
created: 2026-06-21
modified: 2026-10-25
type: meta
title: KB executable endpoints
---

# KB executable endpoints

Use `--help` on the selected helper for exact arguments. Run helpers with the
KB's Python environment and always pass `--kb-root ROOT` when available.
These commands do not replace current user authorization or factual review.

## Task routing

The [write contract](../_rules.md), [formats](../_schema.md) and the selected
[flow](../AGENTS.md) are authoritative. This page locates operations; it is not
another rulebook. Ordinary context retrieval uses the installed `kb-search`
with explicit domain and output budget and needs no Obsidian app or index.

| Operation | Executable owner |
|---|---|
| Scope/identity preflight | `kb-prep-item.py --url URL --skip-media`, `kb-seen.py`, `kb-ingestion-preflight.py` |
| URL capture and section promotion | `kb-capture-url.py`, `kb-promote.py` |
| Queue completion or repair | `kb-checkoff.py` |
| Staged source validation | `kb-validate-source-candidate.py` |
| Permitted extraction evidence | `kb-retain-evidence.py` |
| Exact-draft independent review record | `kb-review-receipt.py --input JSON`, `--verify RECEIPT` |
| New immutable source publication | `kb-commit-source.py` |
| Scoped source replacement | `kb-source-successor.py --manifest JSON` |
| Mechanical source link repair | `kb-repair-source-wikilinks.py` |
| Shared note-write boundary | `kb-write-guard.py` library: `write_lock`, `regular_text`, `atomic_create`, `atomic_write` |
| Activity append | `kb-log-append.py` |
| Derived queue | `kb-derive-dive-queue.py` |
| Queue URL dedup/rehome | `kb-dedupe.py`, `kb-rehome-url.py` |
| Structural conformance | `kb-lint.py` |
| Read-only project Git observation | `kb-project-status.py` |
| Media capture/local inspection/cleanup | `kb-media-capture.py`, `kb-media-inspect.py` capture/inspect/cleanup modes |
| OCR and mutable media backlinks | `kb-ocr-enrich.py`, `kb-link-media-backlinks.py` |
| Selected citation/archive lookup | `kb-resolve-reference.py`, `kb-wayback.py` |
| Book lifecycle | `kb-book-ledger.py` |

Some host-specific helpers exist only in the live KB. Check availability; do
not copy an unreviewed helper from another domain or invent a missing endpoint.
Detailed media CLI contracts sit beside their tools and load only for that task.

## Publication example

Run from the selected KB root, or use absolute script paths.

```sh
python _tools/kb-validate-source-candidate.py --kb-root ROOT \
  --staged-text DRAFT --candidate-path topics/TOPIC/sources/NOTE.md --json
python _tools/kb-review-receipt.py --kb-root ROOT --input REVIEW.json
python _tools/kb-commit-source.py --kb-root ROOT --staged-text DRAFT \
  --candidate-path topics/TOPIC/sources/NOTE.md \
  --queue-file topics/TOPIC/reading-list.md --url URL --review-receipt RECEIPT
```

The review input must match the helper's actual schema and actual independent
comparison. No generated JSON placeholder grants approval. Use `--mode headless`
only for the controller-supplied Auto-approved row. A seen source still needs
validation and current review before a new completion.

The publisher does not fetch, call models, synthesize articles or commit Git.
`SOURCE_AND_QUEUE_COMPLETE` covers source and queue only.
`SOURCE_WRITTEN_QUEUE_PENDING` requires exact-byte reconciliation.

## Enforcement boundaries

The shared parser selects exact primary locators and canonical approval sections.
The write guard checks containment, ownership, symlinks, singly-linked regular
files and cooperative locking. The publisher/checkoff bind final source bytes,
review, queue and locator. Evidence/receipt hashes do not establish factual truth,
authenticated human approval, visual completeness or whole-operation atomicity.

JSON evidence, reviews, successors and controller receipts follow their creating
tools' schemas; do not hand-edit them to satisfy a gate. Failure/interruption
does not authorize retries, deleting staging, or resetting scheduler latches.
For scheduled operation and model routes, use the host's configured runbook
and `_tools/deploy/README.md` when present. No service is enabled here.
