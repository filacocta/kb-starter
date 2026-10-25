---
name: coverage-audit
description: Measure real Python test coverage with coverage.py/pytest-cov, including line and branch coverage, and flag changed or explicitly risk-marked modules below a stated threshold. Use for coverage or code-review audits; do not infer coverage from test counts.
---

# Coverage audit

Give a real, reproducible coverage number and identify risky gaps. Branches
that are entered but not exercised can hide guard failures, so report line and
branch coverage separately and together when the tool supports it.

## Run it

From the target repository root, use its documented test runner and coverage
configuration. A typical pytest-cov invocation is:

  python -m pytest -m "not integration" --cov=src --cov-report=term-missing --cov-report=xml

Respect the repository's own package roots, markers, fixtures, and integration
policy. Do not install dependencies automatically. If the command cannot run,
report the exact missing dependency/configuration and do not invent a number.

The term-missing report identifies uncovered lines and branch transitions such
as 123->exit or ->126. Preserve coverage.xml or the configured equivalent as a
machine-readable artifact when the repository workflow requests it.

## Report

Surface:

1. total line coverage and branch coverage, with the command and scope used;
2. combined/overall coverage only when the measurement tool defines it;
3. changed files and whether new lines/branches are covered for a diff review;
4. the lowest-covered modules, capped to a useful shortlist;
5. explicitly documented risk modules and guard/error paths below the requested
   threshold;
6. test collection/configuration gaps and residual uncertainty.

Choose risk modules from project evidence (changed paths, project docs, security
or data-loss guards, payment/authorization boundaries, or a user-supplied
criticality list). Never hardcode a private project's module names, baseline,
incident, or target floor into this starter skill.

## Thresholds

Use a threshold supplied by the user or repository configuration. If none is
given, report the measured values and label any prioritization as a recommendation,
not a pass/fail gate. A changed risk path with an uncovered new branch is a
review finding; whether it blocks merge is governed by the repository's own
review policy.

## Adjacent tools

Recommend diff-coverage or mutation testing only when the repository already
supports them or the user asks. Do not auto-install or select a provider/model.
Record artifacts and checks in the requested review output. Keep this skill
focused on measurement and evidence, not implementation of missing tests.
