# Team document formats

New notes have `title`, `type`, `status` and `modified` in YAML frontmatter.
Choose descriptive types such as decision, architecture, runbook, research,
plan or session. `modified` is the document edit date. Authorship is honest;
there is no agent-specific author requirement.

Legacy metadata is grandfathered. On substantive edits, add missing fields
where practical, recording unknowns rather than inventing values. Scope, owner
and review status help current guidance. Runbooks state their review status.

Cite evidence near material claims and preserve conflicts. Distinguish source
assertions, observed facts and inference. For current, built, tested or live
claims, add only applicable evidence fields:

- Scope and observation date.
- Repository revision and the code actually inspected.
- Test command, result and artifact.
- Named deployment environment and observed evidence.

Plans and historical reports retain those labels. Git identity alone proves
neither implementation nor deployment. Missing evidence remains unknown.

The vault root is `knowledgebase/`. Wikilinks are relative to that root.
Markdown links use their actual file-relative paths. Review changed links,
frontmatter, privacy and `git diff --check` before a PR. Mechanical conformance
does not establish factual truth or choose canonical ownership.
