# Long-Form Library Model

Use this model when books, reports, manuals, and long articles span multiple KB topics.

## Separation Of Concerns

- **Raw custody:** keep PDFs/EPUBs/text dumps in a library/books area outside topic folders.
- **Catalog metadata:** maintain a lightweight catalog entry with title, author, edition, file path, acquisition status, provenance, and topic links.
- **Topic ingestion:** create topic source notes only for the portions actually read or extracted for that topic.
- **Synthesis:** connect extracted material through concept notes and syntheses, not copied book files.

## Why This Exists

Books rarely map cleanly to one topic. A market-structure book might support prediction markets, trading bots, exchange design, and data visualization. A design book might support dashboards, compapp, UI review, and accessibility. The catalog prevents duplicate custody while topic notes preserve why a specific portion mattered.

## Suggested Pipeline States

- `available`: raw file exists but has not been reviewed.
- `cataloged`: metadata and candidate topics recorded.
- `triaged`: relevant chapters/sections identified.
- `partially-ingested`: at least one topic has source notes or extracts.
- `synthesized`: concepts or syntheses created from extracted material.
- `skill-candidate`: source cluster may support a skill.
- `package-drafted`: candidate skill package exists but is not validated.
- `package-validated`: `SKILL.md`, referenced paths, support files, examples, assets, scripts, and compatibility notes pass a shape check.
- `trigger-tested`: description or invocation criteria have been tested against intended, explicit, adjacent, and out-of-scope prompts.
- `skill-tested`: candidate tested against eval fixtures.
- `security-reviewed`: provenance, external artifacts, scripts, tool permissions, and adversarial trigger risks reviewed.
- `installed-local`: candidate installed for personal use.
- `starter-safe`: candidate is generic, provenance-clean, and suitable for a starter/skeleton repository.
- `superseded`: candidate replaced by newer synthesis or a better package.
- `context-only`: useful background, no skill planned.

## Dashboard Signals

A dashboard lane should surface raw file path, catalog status, candidate topics, current extraction state, promotion decision, candidate skill name, package validation status, trigger-test status, eval status, security-review status, lifecycle state, and blockers.

For KB-backed work, keep this state in topic-owned source catalogs, candidate notes, eval-fixture syntheses, and project todos. Do not rely on the skill package itself as the only lifecycle record.
