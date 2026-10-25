---
name: kb-audit
description: Audit KB structure, exact duplicates, contract drift and selected evidence with read-only checks. Distinguish mechanical findings from semantic judgment.
---

# kb audit

Resolve the task's domain from the installed config. Start with a scoped
read-only audit:

```sh
<python> <tools>/kb-audit --config <config> --domain personal --json
```

For contract maintenance add --contracts. For Personal schema checks use
the selected KB's _tools/kb-lint.py with explicit --kb-root and scope.
Legacy gaps are distinct from new errors. Work follows its own grandfather-aware schema.

Actual corrections/cleanup use _rules.md and _workflows/maintenance.md for Personal,
or the work repository's contract. Hash equality does not select ownership.
Inspect fidelity, stale claims and disagreements separately from parsing/link health.
Do not delete sources, rewrite human text or create cosmetic migrations from lint counts.
Report changed behavior, evidence and remaining uncertainty.
