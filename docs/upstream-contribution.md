# Compare with upstream

The starting point is `sbuffkin/kb-starter` master at
`b3f1d639887c9764b5957031847b186d777cd970`, fetched on 2026-10-25.
The reusable work was originally three commits ahead, touching 167 paths.
This branch collects those improvements and removes the bundled applications.
It starts directly from upstream and does not carry private vault history.

## Review by capability

| Capability | Main files |
|---|---|
| Bounded current-body retrieval, citations, explicit domains and generic team collections | `scripts/kb-search`, `kb_config.py`, `kb_retrieval.py` and synthetic tests |
| Five task skills and verifiable installation/legacy upgrades | `skills/release.json`, `install.py`, `legacy-manifest.json` |
| Short contracts and task-specific procedures | `knowledgebase/AGENTS.md`, `_rules.md`, `_schema.md`, `_workflows/` |
| Team engineering wiki without Personal ingestion requirements | `profiles/team/` |
| Source validation, retained evidence, independent review and guarded publication | `knowledgebase/_tools/` and tests |
| Structural audit, exact duplicates and complete-set contract verification | `scripts/kb-audit`, `kb_contract_spec.json`, `build-contract-manifest.py` |
| Read-only private-to-starter comparison and publication safeguards | `scripts/plan-starter-export.py`, `check-starter-privacy.py` |
| Explicit optional-plugin model selection | `plugin/src/api.ts`, `constants.ts`, `KbBotSettingTab.ts` |

The five task skills replace overlapping query, scan, queue, lint, project-init
and wrap adapters. Relinking remains a deterministic program. Book promotion
and generic coverage remain optional adjuncts outside the installed release.
No account-switching helper is included.

## Application removal

The top-level `projects/` capture and dashboard applications, their service/timer
units, credentials examples, UI payloads and KB project records are removed.
No server, Telegram client, capture database or LAN directory is needed by the
core. This branch does not alter any separately deployed capture service.
Optional applications can be maintained separately from this contribution.

## Compatibility and limits

Personal topic/project paths remain. Live lookup needs no index or Obsidian.
Legacy metadata search remains available after an explicit index build.
Team context accepts a root map or one eligible nested map without an
organization-specific directory name. Project and topic searches stay scoped;
linked evidence expands only when requested.

Existing source bytes and human content are not migrated automatically. Neither
duplicate hashes nor newer dates choose a canonical owner. Human/model review,
tests and environment evidence answer different questions. This is a draft
contribution for maintainer review, not an automatic migration of upstream users.

Reproduce the full diff with `git diff upstream/master...HEAD`. Follow
[verification instructions](portable-hardening.md#verify-changes) rather than
treating a historical pass count as proof of the current branch.
