# Contribute portable KB improvements

## Scope

This branch targets the upstream starter's `master` branch. It keeps Markdown
and Git authoritative, adds bounded retrieval and tested write boundaries, and
provides a separate team-wiki profile. The capture application, dashboard and
their deployment units and project scaffolds are excluded. There is no bundled
scheduler or background service. Models are selected in the user's own harness.
The optional Obsidian plugin remains an explicit Anthropic adapter.

The runtime package version is in `skills/release.json`. The independently
versioned Personal contract file set is in `scripts/kb_contract_spec.json`.
Each KB's `_contract.json` binds that set to its reviewed bytes. Team governance
does not inherit the Personal contract.

## Migrate existing installations

Keep a revision of existing notes and inspect your local changes. Install with
`skills/install.py --dry-run`, then `--status`. Unchanged known legacy adapters
can be removed on upgrade. Customized retired adapters block replacement until
reviewed. Configuration is preserved unless explicitly replaced.

Existing source notes and completed queue rows remain historical. Do not rewrite
or retrospectively certify them. New publication binds a validated draft, exact
locator, inspected evidence, independent review and queue completion. A receipt
proves consistency, not factual accuracy or authenticated human approval.
Use operation receipts to reconcile partial writes before completing the queue.

Project context does not require ingestion. Keep plans, inspected implementation,
test results and environment observations distinct. Preserve useful history and
human edits before shortening summaries or articles. Record source dispositions
in the existing article rather than generating a parallel ledger.

Do not replace an existing team's governance with the example profile. Map its
root explicitly and retain its own metadata, authorship and review process.

## Verify changes

Use an isolated development environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r knowledgebase/_tools/requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -p no:cacheprovider \
  knowledgebase/_tools/tests scripts/tests
.venv/bin/python -B scripts/kb-audit --domain personal --kb-root knowledgebase --contracts
.venv/bin/python -B scripts/check-starter-privacy.py
git diff --check
```

In `plugin/`, `npm test` and `npm run build` verify the optional adapter without
calling a provider. No test requires a live vault, service or account. Use
synthetic/public-safe fixtures. Structural checks cannot verify source truth,
choose canonical ownership or certify a deployment. Record real test output and
independent review before opening an upstream PR. A maintainer decides merging.

## Export boundary

The export helper compares only `scripts/starter-export-paths.txt`. It rejects
symlinked inputs and prints no file contents. It never copies a live vault.
Port selected reviewed files explicitly. Exclude live notes, media, queues,
logs, checkpoints, source caches, receipts, account configuration, local model
routes, scheduler state and populated custody indexes.

Review the outgoing diff and history as well as the current-tree privacy scan.
The scanner reports categories and locations, never suspected values. It is a
regression guard, not proof that arbitrary private material is safe to publish.
