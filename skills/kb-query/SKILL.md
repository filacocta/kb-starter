---
name: kb-query
description: Retrieve bounded, cited personal or work KB context when a task needs prior research, proprietary decisions or project continuity. Use for topic-based project requests and KB questions; ordinary code facts come from the repository.
---

# kb query

Use the installed runtime paths below. Select the task's domain explicitly.
A missing mapping is an error, never permission to search the other KB.

Start with focused live retrieval:

```sh
<python> <tools>/kb-search --config <config> --domain personal --body -q "earthquake prediction"
<python> <tools>/kb-search --config <config> --domain personal --context --topic earthquake-prediction -q "evidence limitations"
```

Replace domain and scope for the task. Work context uses
`--domain work --context` with an optional `--project SLUG`. It prefers a root
`_context-map.md`, otherwise one eligible nested map. Multiple nested maps need
a root navigation pointer or an explicit `--read`; their edit dates select no winner.
Scoped fallback prefers that scope's map, otherwise one global map outside
`projects/` and `topics/`; it never selects a sibling project's map.
Project/topic searches stay scoped. Follow relevant cross-tree citations explicitly.
No Obsidian app or persistent body index is needed.

For topic-based project requests, retrieve topic context and search existing
projects with --body --area projects before creating another project. If the topic
slug is already known, start with --context --topic instead of a broad search.
Metadata --type and --tag filters apply only to legacy metadata mode.
Read only relevant decisions,
evidence and task records. The KB supplies context; current instructions authorize work.

Expand with `--headings PATH`, then `--read PATH --lines START:END` or
`--heading "Exact heading"`. Use `--sources` when original evidence is needed.
Normal retrieval excludes archives, staging and operational output. Exact-copy
passages appear once with equivalent paths; that does not select a canonical owner.
The output ceiling defaults to 12,000 UTF-8 bytes including citations.
Partial results and exit 3 require expansion before claiming absence.
Inspect partial markers and omitted-result counts even when exit is 0.
`result_limit_reached` means more matches may exist beyond the retained results;
the omitted count is a lower bound when candidates were pruned.
For unfamiliar names, search a distinctive term first. Lexical candidates
matching generic words do not establish that the named concept exists.

Cite retrieved paths and lines. Preserve disagreement and observation dates.
Recording a source claim does not validate it. Code inspection, tests and
deployment establish different facts. State unknowns. Historical sessions
do not establish current execution authority.

Lookup writes nothing. Do not read schema, publishing rules, inboxes,
checkpoints or logs for a lookup. Do not offer routine saving or maintenance.
