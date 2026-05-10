---
name: kb-relink
description: Wikilink AND tag enrichment pass over the Obsidian KB. Finds inline mentions of known anchor terms that should be `[[wikilinks]]` but aren't, and frontmatter `tags:` that should be present but aren't, and adds them. Distinct from `/kb-lint` -- that catches BROKEN wikilinks; this catches MISSING ones. Pairs well with `/loop weekly /kb-relink` for cadence enforcement. Trigger on "kb relink", "wikilink enrichment", "enrich wikilinks", "fix missing wikilinks", "relink kb", "tag enrichment", "/kb-relink".
user-invocable: true
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
---

# KB Relink

Sweep the Obsidian KB at `{{KB_ROOT}}/` for two kinds of missing connective tissue and add them:

1. **Missing wikilinks** -- prose mentions an anchor term but doesn't `[[link]]` to it.
2. **Missing tags** -- the note's prose / wikilinks imply a tag the frontmatter doesn't declare.

**Distinct from `/kb-lint`:** kb-lint reports BROKEN wikilinks and structural problems. kb-relink reports/fixes the opposite -- wikilink targets exist, prose just isn't linked; tags exist in the canonical inventory, frontmatter just doesn't declare them.

## Why both passes in one skill

The wikilink pass and the tag pass share infrastructure:

- The same anchor inventory (Step 2) feeds both. The tag pass uses `via_anchor:` clauses in `tag-rules.yaml` to imply tags from already-present wikilinks.
- The same safe-zone parser (Step 4 sub-step 3) keeps tag-trigger phrase scanning out of code blocks / frontmatter / existing wikilinks.
- The same anti-hallucination rule (no proposal without an explicit entry in a curated rule file) governs both.

Running them together also means a single sweep brings a note up to current standards on both connectivity axes, rather than two stale-half-the-time passes.

## When to run

- Ad-hoc when you notice un-linked anchor terms in a recently-written note
- On cadence: `/loop weekly /kb-relink` keeps drift in check
- After a synthesis batch (e.g., after `/kb-synthesise` lands a topic) before declaring the topic "done"

## Arguments

Positional (one of):

- `(no args)` -> full KB sweep
- A topic slug (`video-processing`, `your-topic`) -> scope to `topics/<slug>/`
- A relative path (`topics/video-processing/notes/concepts/`) -> scope to that subdirectory

Flags (additive):

- `--dry-run` -> don't edit; print a per-file report of would-be additions with before/after snippets (default off)
- `--report-only` -> write the would-be additions to `_relink-report.md` at the KB root and exit; no edits
- `--apply` -> default; edit files in place and print a summary diff
- `--max-per-section N` -> override the default once-per-`##`-section heuristic (default 1)
- `--links-only` -> skip the tag-enrichment pass; only fix wikilinks
- `--tags-only` -> skip the wikilink pass; only fix tags

`--dry-run`, `--report-only`, `--apply` are mutually exclusive. If none is given, default is `--apply`. By default both passes run -- `--links-only`/`--tags-only` are mutually exclusive opt-outs for when you want to scope a sweep narrowly.

## KB Location

`{{KB_ROOT}}/`

## Procedure

### Step 0 -- Resumption check

If a sidecar file exists at `<KB_ROOT>/_relink-progress.json`, **resume**:

1. Read it. The expected schema is `{"started": "<ISO timestamp>", "scope": "<scope arg>", "completed_files": [<absolute paths>]}`.
2. If the prior run's `scope` does not match the current invocation's scope, **discard** the sidecar (different invocation -- start fresh).
3. If it matches, treat `completed_files` as the skip-list for Step 4.

If no sidecar exists, create one at the **start** of Step 4 with `completed_files: []`. The skill writes to this sidecar after each file edit (Step 5) so a usage-limit interrupt mid-run is recoverable: the next invocation picks up where the previous one stopped.

The sidecar is **deleted on successful completion** (after Step 7 self-review passes). A user can also delete it manually to force a fresh run.

### Step 1 -- Resolve scope

- No arg -> `KB_ROOT={{KB_ROOT}}`, scan `topics/**/*.md`
- Topic slug -> `topics/<slug>/**/*.md`
- Path -> that path's `**/*.md`

In all cases, exclude:

- `_*.md` (summary, index, checkpoint, schema, rules, dive-queue, todo, relink-report)
- `README.md`, `reading-list.md`
- `.obsidian/`, `.git/`, `.trash/`, `bases/`, `*.base` files
- `_research-inbox/` (raw inbox, not yet curated)
- `topics/personal-notes/notes/daily/*.md` (daily notes are append-only journals; relinking churns them)
- `topics/personal-notes/notes/entities/mark-todos.md` (machine-rewritten by hooks; do not touch)

### Step 2 -- Build the anchor inventory

For every `.md` file in the **full KB** (regardless of scope -- anchors can come from anywhere), extract:

1. **Filename without `.md`** -> the wikilink slug (`pyav-entity`, `h264-deep-dive`)
2. **`title:` from frontmatter** -> primary display phrase
3. **`aliases:` from frontmatter** if present -> additional display phrases (YAML list)

Skip these as anchor sources:

- The exclusion list from Step 1 (we don't want to link TO summary/daily/scratch files)
- Source notes (`topics/*/sources/*.md`) -- sources are immutable historical records; linking inline to them is rarely useful
- Notes without `type:` in frontmatter (likely scratch)

Build a map:

```
anchor_slug -> {
  display_phrases: [phrase1, phrase2, ...],   # title + aliases + heuristic common-noun forms
  type: "entity" | "concept" | "synthesis" | "summary",
  topic: "<topic-slug>",
}
```

### Step 2.5 -- Build the tag inventory (skip if `--links-only`)

The tag-enrichment pass needs three lookups:

1. **Curated rules** -- load `~/.claude/skills/kb-relink/tag-rules.yaml`. Each tag in this file is a candidate for auto-add; tags absent from this file are NEVER proposed (anti-hallucination).

2. **Canonical tag set** -- run `obsidian tags counts` and parse the TSV output. This is the authoritative inventory of every `#tag` currently used in the vault. Use it to:
   - Cross-check that each `tag-rules.yaml` key actually exists in the vault (warn on unknowns -- usually a typo).
   - Surface high-usage tags missing from `tag-rules.yaml` in the Step 6 report under "Suggested rules to curate".

3. **Per-note current tags** -- when scanning a file in Step 4.6, parse the file's `tags:` frontmatter array (and inline `#hashtag` references in the body). The proposal logic only fires for tags that pass all triggers AND are not already present.

### Step 3 -- Add common-noun aliases (heuristic)

Slug-to-phrase isn't enough. `pyav-entity` should also match the bare phrase `PyAV`. Apply these heuristics to expand `display_phrases` for each anchor:

1. **Strip `-entity` / `-deep-dive` / `-overview` suffixes** from the slug, then split on `-`, then re-case.
2. **Load `aliases.yaml`** from this skill directory (`~/.claude/skills/kb-relink/aliases.yaml`) and merge in the curated alias list per anchor slug. The file is the user-extensible seed dictionary.
3. **Drop overly-generic phrases.** Hard-skip these even if a slug matches them: `format`, `container`, `stream`, `frame`, `packet`, `protocol`, `model`, `video`, `audio`, `data`, `service`, `pipeline`, `system`, `library`, `api`, `client`, `server`, `agent`, `note`, `source`, `topic`. These are too common in prose to safely link.
4. **Skip slug-heuristic for synthesis-type anchors.** Synthesis filenames are usually long compound phrases whose slug components are owned by more-specific entity/concept anchors. **Synthesis anchors only contribute aliases via explicit `aliases:` frontmatter or `aliases.yaml` entries**; do not slug-split them.

The `aliases.yaml` file is the long-term curation surface. When you find a phrase the heuristic missed, add it there rather than to this SKILL.md.

### Step 4 -- Scan files in scope for unlinked occurrences

For every `.md` file in scope (Step 1):

1. **Read the file.**
2. **Strip the YAML frontmatter** (between leading `---` lines) -- never touch it.
3. **Identify "safe" zones** that prose-matching must skip:
   - Fenced code blocks (` ``` ... ``` `, including language tags)
   - Indented code blocks (4-space indent at line start)
   - Inline backticks (`` `...` ``)
   - Existing wikilinks (`[[...]]`, including aliased form `[[target|display]]`)
   - Markdown links (`[text](url)`) -- don't relink the `text` portion
   - Bare URLs and file paths (lines starting with `http`, or paths with `/`)
   - HTML/Obsidian comments (`<!-- ... -->`)
4. **For each anchor** (Step 2 + 3) where the **current file is not the anchor's own source**:
   - Skip if the file's own slug == the anchor's slug (don't self-link)
   - Skip if the file's `title:` matches one of the anchor's display phrases (likely the anchor's own page)
   - For each `display_phrase`, find prose occurrences (case-sensitive for proper-noun-ish phrases like `PyAV`, case-insensitive for lower-case slugs)
   - Use word-boundary matching (`\b<phrase>\b`) -- don't match inside identifiers like `pyav_decoder`
5. **Apply the once-per-section heuristic:**
   - For each `##`-level section in the file, link only the **first** occurrence of each anchor's phrases.
   - The lead paragraph (before the first `##`) counts as its own section.
   - `--max-per-section N` overrides this if set higher.

### Step 4.5 -- Anti-hallucination verification (BEFORE applying any edit)

For every proposed `(file, line, phrase, anchor-slug)` edit:

1. **Look up the anchor's `display_phrases`** from the inventory built in Step 2 + Step 3.
2. **Verify** the matched `phrase` is in that list (case-insensitive comparison). If not, **skip the edit** and add the proposal to a `skipped_unverified` list for the Step 6 report.
3. **Reject ad-hoc semantic links.** Do NOT link a phrase to an anchor based on subject-matter similarity if the alias map doesn't authorize it. The map IS the authority.

### Step 4.6 -- Scan files in scope for missing tags (skip if `--links-only`)

For every `.md` file in scope, after the wikilink scan completes:

1. **Parse frontmatter `tags:`** -- the YAML array (or null/missing). Normalize to a set of bare tag strings (with leading `#` stripped, lowercase). This is the file's current tag set.

2. **Determine the file's link set** -- collect every `[[anchor-slug]]` or `[[anchor-slug|...]]` reference in the body (post-edit, if the wikilink pass just ran). Strip the alias half. This is the file's outgoing-anchor set; needed for the `via_anchor:` rule.

3. **For each tag in the inventory:**
   - Skip if the tag is already in the file's current tag set.
   - **Phrase trigger:** if any phrase from the tag's `phrases` list appears in the file's body using the same safe-zone rules, the tag fires.
   - **Anchor trigger:** if any slug in the tag's `via_anchor` list is present in the file's outgoing-anchor set, the tag fires.
   - **Self-tag exemption:** if the file's own slug appears in the tag's `via_anchor` list, do NOT propose the tag for that file.

4. **Collect proposals** -- `(file, tag, trigger_type, evidence)` where `trigger_type` is `"phrase"` or `"via_anchor"` and `evidence` is the matching phrase or anchor slug.

### Step 4.7 -- Anti-hallucination verification for tag adds (BEFORE applying any tag edit)

Mirror Step 4.5's logic for tags. The curated rule file is the single source of truth, and proposals that don't trace back to it are dropped silently rather than applied.

### Step 5 -- Apply edits

For each file with proposed edits (wikilinks and tags) -- apply per the chosen mode (`--apply`, `--dry-run`, `--report-only`).

When linking, always use the **aliased form** `[[anchor-slug|original-phrase]]`, even if the phrase exactly matches the slug. Reasoning: it preserves the original casing/punctuation of the prose, and the explicit pipe makes the link target obvious to a future reader skimming the diff.

### Step 6 -- Report

After processing, print to stdout (and write to `_relink-report.md` if `--report-only`):

```
# KB Relink Report -- {YYYY-MM-DD HH:MM}

Scope: {scope description}
Mode: {apply | dry-run | report-only}
Passes: {wikilinks + tags | wikilinks-only | tags-only}

## Summary

- Files scanned: {N}
- Files modified: {M}
- Wikilinks added: {total count}
- Tags added: {total count}
- Anchors with no candidates found: {K}
- Tags with no candidates found: {Tk}
- Wikilink edits skipped (Step 4.5): {S}
- Tag edits skipped (Step 4.7): {ST}
- Resumed from prior run: {true | false}

## Top anchors by wikilink additions
## Top tags by additions
## Per-file changes
## Orphan anchors (defined but never mentioned in scope)
## Skipped (wikilink anti-hallucination)
## Skipped (tag anti-hallucination)
## Suggested rules to curate
```

### Step 7 -- Self-review

Before returning:

1. Sanity-check that no wikilink edit landed inside a frontmatter block, code fence, or existing wikilink. If any did, revert that edit and flag it.
2. Sanity-check that no tag edit produced unparseable YAML.
3. If `--apply` was used and >50 files were modified in a single run, warn -- a sweep that touches that many files in one go is suspicious.
4. If any anchor produced >20 wikilink additions in a single file, flag it -- usually means a phrase too generic slipped into `aliases.yaml`.
5. If any single tag was added to >10 files in one run, flag it -- the rule may be too broad.

## Rules

- **Never touch the wikilink frontmatter, code blocks, or existing wikilinks.**
- **Tag edits ONLY touch the `tags:` field in frontmatter.**
- **Never link to the anchor's own page.** No self-links.
- **Never tag a note that IS the anchor's own page.**
- **First mention per section only** (default, wikilinks).
- **Use the aliased form `[[slug|phrase]]`** so the original prose surface is preserved.
- **Aliases live in `aliases.yaml`**, not in this SKILL.md.
- **Tag rules live in `tag-rules.yaml`**, not in this SKILL.md.
- **Skip generic common nouns.**
- **Skip source notes as anchor targets and tag-enrichment targets.**
- **Don't sweep daily notes or mark-todos.** Append-only / machine-managed.

## Execution: invoke the committed driver

The skill's logic is implemented as a deterministic Python driver at:

```
~/.claude/skills/kb-relink/relink.py
```

This is the canonical execution path. **Do not re-implement the logic per-invocation in `/tmp`** — historically that approach dropped 80% of files due to agent-driven file-by-file inference. The committed driver eliminates that variance.

### How to call it

```bash
# Default: dry-run, both passes, full KB
~/.claude/skills/kb-relink/relink.py

# Dry-run on a single topic
~/.claude/skills/kb-relink/relink.py <topic-slug> --mode dry-run

# Apply on a topic (writes to disk)
~/.claude/skills/kb-relink/relink.py <topic-slug> --mode apply

# Tag enrichment only — no wikilink edits
~/.claude/skills/kb-relink/relink.py <topic-slug> --tags-only --mode dry-run

# Wikilinks only — no tag edits
~/.claude/skills/kb-relink/relink.py <topic-slug> --links-only --mode apply

# Markdown report at <KB>/_relink-report.md
~/.claude/skills/kb-relink/relink.py <topic-slug> --mode report-only
```

The driver loads `aliases.yaml` and `tag-rules.yaml` from its own directory, builds the anchor inventory by walking the KB once, scans the scoped files, and emits a JSON summary to stdout. In `--apply` mode it writes edits in place after a YAML safety check on tag edits.

### Confidence thresholds (Pass 2 / tags)

The tag pass requires:

- **`PHRASE_TRIGGER_MIN`** safe-zone phrase matches (default 5) — a single passing mention isn't enough; a note that uses the term across multiple sections is.
- **`VIA_ANCHOR_TRIGGER_MIN`** distinct anchors from a tag's `via_anchor:` list (default 2) — a note that links to ONE entity in a topic shouldn't get the topic's tag; linking to two or more is structural evidence.

### When to edit the driver vs the rules

- **Anchor or tag did not propose where it should have:** add to `aliases.yaml` or `tag-rules.yaml`. No driver change needed.
- **Class of false positive:** edit `relink.py` Step 4 sub-step 3 / `find_unsafe_spans` and add the pattern.
- **Confidence thresholds feel wrong:** edit the constants in `scan_file_tags`.
- **New trigger type for tags:** extend `TagRule` and `scan_file_tags`. Update `tag-rules.yaml` schema to match.

## Tooling fallback (rg shadowing)

The Grep tool relies on ripgrep, which can be shadowed by a shell function named `rg` in the user's environment. If Glob / Grep return ENOENT or similar errors when targeting `{{KB_ROOT}}/`, fall back to **Bash** with `find` and `grep -rn`.

## Cadence integration

This skill is designed to be safe to run regularly. Pair with:

```
/loop weekly /kb-relink
```

to enforce wikilink discipline as the KB grows.

## Related

- [[skill-kb-lint]] -- complementary; finds BROKEN wikilinks (target missing). kb-relink finds MISSING wikilinks (target exists, prose unlinked).
- [[skill-kb-synthesise]] -- often produces notes that benefit from a follow-up `/kb-relink` pass once the synthesis lands.
- [[skill-kb-recap]] -- can be combined: recap recent changes, then relink only the changed files via the path-scope argument.
