---
name: coverage-audit
description: Measure REAL Python test coverage (coverage.py via pytest-cov) for a repo, report the total line+branch %, and flag pricing-critical / changed modules below a threshold. Use standalone ("coverage audit", "what's our test coverage", "coverage %") or as a sub-step of a code-review/audit before signing off on a diff. Trigger on "coverage audit", "code coverage", "coverage %", "test coverage", "/coverage-audit".
user-invocable: true
---

# Coverage audit

Give a real, defensible test-coverage number and point at the risky gaps — never eyeball
"looks covered." Coverage that's *entered* but whose guard branches are never exercised is
the class that let the povar board-visibility read-filter bug through, so **branch coverage
is on** and matters as much as line coverage.

## Run it

From the repo root (povar / any repo with pytest-cov + a `[tool.coverage.*]` config in
pyproject.toml):

```bash
uv run pytest -m "not integration" --cov=src --cov=app --cov-report=term-missing --cov-report=xml
```

- `-m "not integration"` — integration tests need testcontainers / live infra; exclude them
  for a fast, deterministic local number (CI runs the full set).
- `--cov-report=term-missing` — prints each module's % **and the uncovered line/branch ranges**
  (a `123->exit` or `->126` entry is an unexercised branch — read those on guard code).
- `coverage.xml` — machine-readable, for diff-coverage / CI artifacts.

## Report (what to surface, not raw dumps)

1. **TOTAL %** (the bottom line — line+branch combined).
2. **Pricing-critical modules** and their %: `src/{first_half,nfl_ev,donbest_ev,pipeline,
   cdn_ladders,ev,prob,cdf}.py`, `src/lib/db.py`, `app/routers/{opportunities,internal,run,
   first_half_view,football_alts}.py`. Any pricing/guard path under ~70% is a risk — name it.
3. **The 10-15 lowest-covered modules** with %, so the backfill has a target list.
4. When reviewing a DIFF: run coverage, then check the CHANGED files' % and whether the
   NEW lines are covered. A changed pricing file whose new branch is uncovered is a
   review blocker — say so. (Diff-coverage tooling, below, automates this.)

## Threshold guidance

- povar baseline (2026-09-13): **~77%** total. Target floor once the backfill lands: **70%**
  (soft in CI today — no `fail_under` yet; see CLAUDE.md "CI & Testing"). Flip
  `[tool.coverage.report] fail_under = 70` when ready to gate.
- A NEW or CHANGED pricing/guard path should ship with a test that covers it (both branches).

## Adjacent tools worth reaching for (recommend, don't auto-install)

- **diff-cover** (`diff-cover coverage.xml --compare-branch=origin/master`) — % coverage of
  only the lines a PR changed. The single highest-value add: it turns "did you test what you
  just wrote?" into a number, and catches the exact class of miss above.
- **mutmut / cosmic-ray** (mutation testing) on the money-math (`src/{ev,prob,cdf,
  runline_fair}.py`, ladder/fair construction) — proves the tests actually *assert* behavior,
  not just execute lines. Slow; run periodically, not per-PR.
