---
name: kb-lint
description: Run the kb-lint Python script to health-check the KB. Replaces the old LLM-based skill. Accepts --topic, --json, --md, --stale-days, --no-orphans flags.
user-invocable: true
---

The LLM-based kb-lint skill has been retired. The `kb-lint` Python script handles all structural checks without an LLM call.

When invoked, run:

```bash
kb-lint $ARGUMENTS
```

Display the full output to the user. If `$ARGUMENTS` is empty, run `kb-lint` with no flags (full scan, plain-text report).

Available flags (pass through from user args as-is):
- `--topic <slug>` — restrict scan to one topic
- `--json` — JSON output
- `--md` — Markdown output
- `--stale-days N` — staleness threshold (default 30)
- `--no-orphans` — skip orphan detection
- `--errors-only` — flag only errors, not warnings

After displaying results, offer to help resolve any errors listed.

## Companion: kb-fix

`kb-fix` is the autofix companion script. After a lint run, suggest it for mechanical fixes:

```bash
kb-fix                          # dry-run preview of all fixers
kb-fix --fix all                # apply all fixers
kb-fix --fix unrendered_math    # one fixer only
kb-fix --fix all --dry-run      # preview without writing
```

Fixers available:
- `unrendered_math` — `LaTeX: \`\cmd\`` → `$$\cmd$$` display math (addresses `unrendered_math` warning)
- `missing_author` — inserts `author: kb-bot` in frontmatter (addresses `missing_author` info)
- `infer_frontmatter` — adds missing `type:` and `topic:` inferred from path (addresses `missing_frontmatter_field` error)
- `stub_frontmatter` — prepends minimal frontmatter to files with none at all (addresses `missing_frontmatter` error)

Fixers that cannot be automated (require human judgement): `broken_wikilink`, `orphan`, `stale`, `source_modified`.
