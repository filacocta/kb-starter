---
author: kb-bot
created: <ISO-8601 date>
modified: <ISO-8601 date>
type: meta
topic: <project-slug>
title: <Project Name> — Session handoff <date>
description: Dated outcome, evidence, limits and safe resumption for <Project Name>.
context_role: session
---

# <Project Name> — Session handoff <date>

## Outcome and scope

<What the requested work achieved; distinguish completed, deferred and blocked.
Name the KB/project and approved write scope.>

## Observed code and verification

<Repository/working directory, branch and full commit observed at a timezone-aware
time. State dirty/untracked limitations without copying secrets or unrelated
private paths. Describe the files/behavior actually inspected.>

<Exact checks, results and evidence artifacts. Say explicitly when checks were
not run, failed, or covered only metadata. Never infer deployment from local
Git state or tests.>

## Decisions and remaining uncertainty

<Dated user decisions and rationale, alternatives, unresolved conflicts and
what remains unknown. Link prior decisions rather than overwriting history.>

**Externally verified:** <Evidence and environment, or not verified.>

## Resume safely

<Matching checkpoint/run record and task board; next safe action or exact command
with its working directory, owner and gate. A command is guidance, not new
execution authority. Keep ingestion separate unless it is in the requested scope.>

## Later corrections

<Append dated corrections or superseding handoff links; preserve this session's
original evidence and limitations. Omit this empty section until needed.>
