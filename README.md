# kb-starter

A Markdown knowledge base for agents and people. Maintain source-backed research
articles or a team engineering wiki, retrieve bounded context, and keep decisions
and project history useful. The file-based core needs Python and an agent that
can read local files. It needs no running app, bot, model server or vector database.

This contribution builds on [sbuffkin/kb-starter](https://github.com/sbuffkin/kb-starter).
The optional Obsidian plugin remains a separate Anthropic adapter. The bundled
capture service and workflow dashboard are removed from this core branch.

## Choose a KB

For a personal research KB, copy the seeded `knowledgebase/` to your destination.
It separates retained sources, maintained articles and project continuity.
[Its entry point](knowledgebase/AGENTS.md) routes lookup and selected write flows.

For an engineering wiki, copy `profiles/team/AGENTS.md`, `profiles/team/SCHEMA.md`
and `profiles/team/knowledgebase/` into a new repository. The team profile has no
Personal authorship guard, feed, intake queue or source-publication requirement.
Use it as guidance for a new wiki; do not overwrite an existing team's contract.

## Install and query

Use Python 3.12 or newer. From this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r knowledgebase/_tools/requirements.txt
.venv/bin/python skills/install.py --agent codex \
  --personal-root /absolute/personal/knowledgebase
.venv/bin/python skills/install.py --agent codex --status
```

Use `--agent claude` or `--agent custom --destination PATH` for another runtime.
For a team-only installation supply `--work-root /absolute/team/knowledgebase`
instead of `--personal-root`. The installer reports paths in each installed skill.
Existing configuration is preserved; inspect it before using `--force-config`.
Installed commands retain the Python interpreter and dependency environment used
for installation. Keep that environment available or reinstall from its replacement.
`--dry-run` writes nothing. `--status` checks the release and installed hashes.
Modified or customized retired packages require review before replacement.

```sh
.venv/bin/python /path/to/kb-tools/kb-search --config /path/to/kb-config.md \
  --domain personal --context --topic obsidian -q "local files"
.venv/bin/python /path/to/kb-tools/kb-search --config /path/to/kb-config.md \
  --domain work --context
.venv/bin/python /path/to/kb-tools/kb-search --config /path/to/kb-config.md \
  --domain work --body -q "deployment decision" --json
```

Domains have separate explicit roots. Missing configuration never selects the
other domain. Global body search covers arbitrary Markdown collections. Topic
and project selectors remain scoped. Work context prefers `_context-map.md` at
the selected root, otherwise one eligible nested map. Ambiguity requires a root
pointer or explicit file reading. Context without a query reads entry points only.
Scoped fallback prefers that project's map, otherwise a unique global map outside
`projects/` and `topics/`. It never loads a sibling project's map implicitly.

Expand with `--headings PATH`, then `--read PATH --heading "Exact heading"` or
`--lines START:END`. Citations include original lines and SHA-256. Use
`--expect-sha256 HASH` to reject changed evidence. Sources need `--sources`.
Operational output, archives, hidden directories and governance instructions
stay out of ordinary results. Explicit operational reads need `--include-excluded`.

Output defaults to 12,000 UTF-8 bytes, including citations and JSON encoding.
Inspect clipping and omitted-result markers even on exit 0.
`result_limit_reached` signals matching content beyond the retained results;
omitted counts are lower bounds after pruning. Read errors or scan limits
produce incomplete retrieval with exit 3. Exit 1 means a completed lexical
search had no matches; it does not prove a concept absent. Reads are capped at
2 MiB per file, 64 MiB total and 20,000 traversal entries. Directory enumeration
is not a hard memory bound. No lookup builds an index, writes a log or starts an app.

## Task skills and contracts

| Skill | Purpose |
|---|---|
| `kb-query` | Read-only topic, project and evidence context. |
| `kb-project` | Existing-project discovery, context, init and wrap. |
| `kb-audit` | Structure, exact duplicates, contract drift and selected evidence. |
| `kb-ingest` | Personal source publication through validation and independent review. |
| `kb-synthesise` | Personal article maintenance with citations and conflict handling. |

Only retrieve KB context missing from the prompt and relevant code. Skill
discovery does not guarantee every session consults the KB. Prompt caching is
separate from durable knowledge. Query instructions do not load write contracts.

The Personal contract has one short shared policy, data schemas and task-specific
`_workflows/` pages. Skills route to those pages instead of repeating procedures.
Publication uses deterministic validation, evidence and exact-draft review
receipts. A successful publication does not establish truth or completed synthesis.
Team KBs retain their own review, authorship and evidence contracts.

## Audit and maintain

```sh
.venv/bin/python scripts/kb-audit --domain personal --kb-root knowledgebase --json
.venv/bin/python scripts/kb-audit --domain personal --kb-root knowledgebase --contracts --json
```

The first command reports structural and duplicate evidence. Hash equality does
not select ownership or authorize deletion. Work metadata checks are advisory
helpers for the supplied team profile, not a universal schema for every team.
The contract check requires its complete known file set and checks reviewed-byte
hashes, links, retired references and size. It does not prove semantic consistency.
After reviewing an intentional Personal contract edit, explicitly run
`scripts/build-contract-manifest.py --kb-root ROOT` to record it.

Maintain readable articles selectively. A source can change an article, add a
bounded disagreement or make no material contribution. Preserve human edits and
source bytes. No per-source page quota, global startup scan or unsolicited triage
is required. Retrieval indexes remain derived and optional.

## Optional Obsidian plugin

The Markdown core does not require Obsidian. To use the in-vault Anthropic adapter,
run `npm install` and `npm run build` in `plugin/`, then copy `main.js` and
`manifest.json` to your vault's `.obsidian/plugins/kb-bot/`. Enable it in Obsidian.
Choose your available model ID and API key explicitly. API billing is separate
from agent subscriptions. The plugin expects `knowledgebase/` inside its vault
and retains its own write guard. This adapter is not the team-profile writer.

## Develop and contribute

See [the contribution guide](docs/portable-hardening.md) for migration, tests and
export review. The [upstream comparison](docs/upstream-contribution.md) groups
the changes for review. `scripts/sync-from-live.sh` is a read-only comparison
planner, not a recursive copy operation. No live notes, custody payloads, logs,
receipts, private configuration or scheduler state belong in a contribution.

## License

MIT, as in the upstream starter. Existing authorship remains intact.
