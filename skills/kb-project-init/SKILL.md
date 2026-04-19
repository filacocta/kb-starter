---
name: kb-project-init
description: Create a new KB project entry for a named project. Reads the project source (codebase, vault notes, or description), then writes projects/<slug>/_summary.md using the standard template. Run this when starting substantive work on a project that has no KB entry yet.
user-invocable: true
---

You are creating a new KB project entry for a project that does not yet have one.

## KB Location

KB root: `/path/to/your/vault/knowledgebase/`

**Project:** $ARGUMENTS

---

## Procedure

1. Read `_index.md` — confirm no entry exists for this project yet. If one exists, stop and tell the user.
2. Determine the project slug (lowercase-hyphenated form of the name).
3. Locate source material:
   - If a vault path is mentioned in $ARGUMENTS, read relevant notes there.
   - If a codebase path is mentioned, read `CLAUDE.md`, `README.md`, key source files to build a picture of the stack and status.
   - If neither, ask the user for a brief description before proceeding.
4. Write `projects/<slug>/_summary.md` using the template below.
5. Add a row to the Projects table in `_index.md` (keep the table sorted by last-updated descending).
6. Append an entry to `_log.md`: date, project slug, "created KB project entry".

---

## Template

```markdown
---
author: kb-bot
created: <today>
modified: <today>
type: summary
topic: projects
status: active
tags: [<relevant tags>]
---

# <Project Name> — Project Summary

**Vault location:** `<vault-relative path, or "n/a">`
**Code location:** `<absolute path, or "n/a">`
**Status:** <one line: prototype / active development / etc.>

---

## Orientation

<2–4 sentences: what the project is, who it is for, the core value proposition.>

---

## Stack

| Layer | Technology |
|---|---|
| ... | ... |

---

## Key Components

<One paragraph or bullet list per major component.>

---

## Implementation Status

### Done
- [x] ...

### Open / TODO
- [ ] ...

---

## Related KB Topics

- [[knowledgebase/topics/<slug>/_summary]] — <one-line reason>
```

---

## Authorship Guard

Only write to files under `knowledgebase/`. Do not modify any file that lacks `author: kb-bot` frontmatter except `_index.md` and `_log.md` (which are KB infrastructure files).
