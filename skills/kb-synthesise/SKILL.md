---
name: kb-synthesise
description: Synthesise source notes for a topic into wiki articles. Reads all source notes, builds a concept/entity inventory, applies the page-creation threshold (stub for 1 source, full article for 2+), presents an approval plan, then writes concept/entity/synthesis notes and updates summaries. Use after running /kb-queue to turn raw sources into a browsable wiki.
user-invocable: true
---

## Preferred Synthesis Method

**For first-pass stub and entity generation across many topics, delegate to the Gemini CLI KB Synthesiser instead of running this skill yourself.**

The Gemini CLI agent is the default for breadth synthesis: it reads all source files in a topic, writes concept stubs and entity profiles, and costs zero Claude tokens. Claude then reviews and enriches.

**Dispatch pattern** (run from KB vault root):
```bash
cd "<KB_ROOT>" && gemini --yolo --output-format text -p "<agent profile prompt from projects/llm-orchestrator/notes/agents/kb-synthesiser.md>"
```

**Use this `/kb-synthesise` skill instead when:**
- A topic has 3+ sources and needs full concept articles with synthesis (not just stubs)
- Cross-topic canonical note detection is needed
- The user wants to review and guide the synthesis before anything is written
- The user explicitly invokes `/kb-synthesise`

After the Gemini agent writes stubs/entities, Claude must: update `_summary.md` Key Concepts/Entities sections, increment note counts in `_index.md`, append to `_log.md`.

---

You are acting as a wiki synthesis agent for a personal knowledge base. Your job is to read the source notes accumulated for a topic and generate well-written concept articles, entity profiles, and synthesis notes from them — turning a folder of raw sources into a navigable wiki.

## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links).

---

## Startup

1. Check `_checkpoint.md` — if status is `in-progress` or `resumable`, surface it and offer to resume.
2. Read `_schema.md` and `_index.md`.

**Argument:** $ARGUMENTS

- If a topic slug was given, proceed with that topic.
- If no argument: "Which topic should I synthesise? (provide a topic slug, or 'all' to process all topics)"

---

## Phase 1 — Read Sources

Read `topics/<topic>/_summary.md` to load existing KB context for this topic.

Then read every source note in `topics/<topic>/sources/`. For each note, extract:
- Title of the source
- All concepts mentioned (proper-noun ideas, named algorithms, named models, named theorems, named phenomena)
- All entities mentioned (people, organisations, tools, projects, games)
- Any wikilinks already used in the source note (e.g. `[[notes/concepts/...]]`)

Build two inventories:

**Concept inventory** — for each distinct concept:
```
- <concept-slug>: <human-readable name>
  Sources: [source-slug-1, source-slug-2, ...]
  Existing note: yes/no (check if topics/<topic>/notes/concepts/<concept-slug>.md exists)
```

**Entity inventory** — for each distinct entity (person, org, tool, project):
```
- <entity-slug>: <human-readable name> (<type: person|org|tool|project>)
  Sources: [source-slug-1, ...]
  Existing note: yes/no
```

---

## Phase 2 — Cross-Topic Check

Before planning new notes, check for canonical notes in other topics:

1. Search `_index.md` for topics with overlapping subject matter.
2. For concepts that might already have a canonical note elsewhere (e.g. a concept that appears in both `evolutionary-algorithms` and `cellular-automata`), check `topics/<other-topic>/notes/concepts/` for an existing note.
3. If a canonical note exists in another topic, do NOT duplicate it — plan a cross-topic link instead.

---

## Phase 3 — Synthesis Plan

Present the plan to the user before writing anything:

> ## Synthesis plan for `<topic>`
>
> ### Full articles to write (2+ sources)
> These concepts and entities appear in multiple sources and warrant a full wiki article (500–1,500 words):
>
> | Note | Type | Sources | Path |
> |---|---|---|---|
> | Genetic Algorithm | concept | 3 sources | `notes/concepts/genetic-algorithm.md` |
> | NEAT | concept | 2 sources | `notes/concepts/neat.md` |
> | DEAP | entity (tool) | 2 sources | `notes/entities/deap.md` |
>
> ### Stubs to create (1 source)
> These appear in only one source. A stub (frontmatter + one-line definition + source backlink) will be created:
>
> | Note | Type | Source | Path |
> |---|---|---|---|
> | CMA-ES | concept | 1 source | `notes/concepts/cma-es.md` |
>
> ### Cross-topic links (no new note)
> These already have canonical notes in another topic:
>
> | Concept | Existing canonical note |
> |---|---|
> | Neural network | `topics/large-language-models/notes/concepts/neural-network` |
>
> ### Synthesis notes (cross-cutting analyses)
> Worth writing when 2+ concepts intersect in a way not captured by any individual concept note:
>
> | Note | What it covers | Path |
> |---|---|---|
> | EA landscape navigation | How fitness landscapes determine which EA strategies work | `notes/syntheses/ea-landscape-navigation.md` |
>
> ### Existing notes to update with backlinks
> These notes already exist and should have backlinks appended (append-only):
>
> | Note | Backlink to add |
> |---|---|
> | `notes/concepts/neuroevolution` (stub) | Add link to new `neat` article |
>
> ---
> Approve all? Or tell me what to skip, rename, or change before I write.

Wait for approval. User can:
- Say "yes" / "approve all" → proceed
- Skip items by name → omit them
- Request changes → revise the plan and re-show
- Say "stubs only" / "full articles only" → filter accordingly

---

## Phase 4 — Write Checkpoint

Before writing any notes:

```markdown
---
author: kb-bot
created: <ISO datetime>
modified: <ISO datetime>
type: meta
---

# Checkpoint

**Operation:** Synthesise — <topic>
**Started:** <ISO datetime>
**Last updated:** <ISO datetime>
**Status:** in-progress

## Queue

- [ ] notes/concepts/<slug>.md (full article)
- [ ] notes/concepts/<slug>.md (stub)
- [ ] notes/entities/<slug>.md
- [ ] notes/syntheses/<slug>.md
- [ ] _summary.md (update note count)
- [ ] _index.md (update note count)

## Completed

*(none yet)*
```

---

## Phase 5 — Write Notes

Work through the approved list in this order: full articles → stubs → synthesis notes → backlink updates.

**After each write, update the checkpoint** (move item from Queue to Completed, update `**Last updated:**`).

**Pause point after every 5 notes** (or earlier if the user requests):
> Written N of M notes. Continue? (yes / pause / stop)
>
> *Tip: type "pause" to save progress and resume in a new session with `/kb-synthesise <topic>`*

---

### Full article format (2+ sources, 500–1,500 words)

Path: `topics/<topic>/notes/concepts/<concept-slug>.md`

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: concept
topic: <topic-slug>
title: "<Concept Name>"
description: "<One sentence: what this concept is and why it matters.>"
status: draft
tags: ["<tag1>", "<tag2>"]
sources: ["[[sources/<source-1>]]", "[[sources/<source-2>]]"]
related: ["[[notes/concepts/<related>]]", "[[notes/entities/<related>]]"]
---

# <Concept Name>

<One or two sentences establishing what this concept is and why it matters. Bold the key term on first use.>

## <Section>

<Prose, not bullet dumps. 2–5 paragraphs per major aspect. Use $...$ inline and $$...$$ block LaTeX for any mathematical content. Wikilink to related concepts: [[notes/concepts/related-slug]]. Cite sources inline: [[sources/source-slug]].>

## See Also

- [[notes/concepts/related-a]] — brief reason
- [[notes/entities/entity-name]] — brief reason
```

**Quality rules:**
- One central concept per note. If you find yourself writing about two distinct things, split into two notes.
- Every factual claim must trace to a `[[sources/...]]` wikilink.
- Link only the first occurrence of each concept per section.
- Prose, not bullet dumps — write as an encyclopaedia article, not a list.
- Bold the concept name on first use; bold other key terms on first introduction.
- LaTeX for all mathematical/symbolic content.
- `status: draft` on creation.

**Contradiction handling:** If two sources contradict each other on a point, note it explicitly:
> ⚠️ **Contradiction:** [[sources/source-a]] states X; [[sources/source-b]] states Y. This may reflect [different definitions / different contexts / a genuine dispute]. See `_discussion.md` for notes.

---

### Stub format (1 source)

Path: `topics/<topic>/notes/concepts/<concept-slug>.md`

**Tags rule:** Always populate `tags` with structural tags + at least 2 content tags derived from the concept's title and description. Never leave `tags: []`. Structural tags: the topic slug, "concept". Content tags: what the concept *is* (e.g. rule-30 → `["concept", "cellular-automata", "elementary-ca", "wolfram-code", "class-2-ca"]`).

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: concept
topic: <topic-slug>
title: "<Concept Name>"
description: "<One sentence definition.>"
status: draft
tags: ["concept", "<topic-slug>", "<content-tag-1>", "<content-tag-2>"]
sources: ["[[sources/<source-slug>]]"]
related: []
---

# <Concept Name>

<One sentence: what this concept is and why it matters.>

*This is a stub. A full article will be written once this concept appears in two or more sources.*

**Source:** [[sources/<source-slug>]]
```

---

### Entity note format

Path: `topics/<topic>/notes/entities/<entity-slug>.md`

**Tags rule:** Always populate `tags` with: "entity", the topic slug, the entity-type value, plus 1-2 content tags (e.g. a person's role or field). Never leave `tags: []`. Example: john-keel → `["entity", "folklore-and-bestiaries", "person", "paranormal-investigator", "mothman"]`.

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: entity
topic: <topic-slug>
title: "<Entity Name>"
description: "<One sentence: who/what this entity is and why it is relevant to this topic.>"
entity-type: person|organisation|tool|project|game|text|place|creature
status: draft
tags: ["entity", "<topic-slug>", "<entity-type>", "<content-tag-1>"]
sources: ["[[sources/<source-slug>]]"]
related: []
---

# <Entity Name>

**Type:** person | organisation | tool | project
**Topic relevance:** <one sentence why this entity matters for this topic>

## Profile

<2–5 sentences: key facts, founding date, creator, purpose, notable outputs. Cite sources.>

## Relevance to <topic>

<Why this entity matters specifically to this topic. Link to related concept notes.>

## See Also

- [[notes/concepts/...]] — brief reason
```

---

### Synthesis note format

Path: `topics/<topic>/notes/syntheses/<synthesis-slug>.md`

**Tags rule:** Always populate `tags` with: "synthesis", the topic slug, plus 2-3 tags capturing the cross-cutting theme addressed. Never leave `tags: []`.

```markdown
---
author: kb-bot
created: <YYYY-MM-DD>
modified: <YYYY-MM-DD>
type: synthesis
topic: <topic-slug>
title: "<Synthesis Title>"
description: "<One sentence: what cross-cutting question or comparison this note addresses.>"
status: draft
tags: ["synthesis", "<topic-slug>", "<theme-tag-1>", "<theme-tag-2>"]
sources: ["[[sources/...]]", "[[sources/...]]"]
related: ["[[notes/concepts/...]]"]
---

# <Synthesis Title>

<2–3 sentences introducing the cross-cutting question or tension this note addresses.>

## <Section>

<Analysis prose, with source citations and concept links throughout.>

## Implications

<What this synthesis means for the user's work or for the broader topic.>
```

---

### Appending backlinks to existing notes

When an existing note should link to a new note, **append only** — never rewrite content.

Check the authorship guard first (`author: kb-bot` must be present). Then append to the `## See Also` section (or add one if missing):

```markdown
- [[notes/concepts/<new-note>]] — brief reason for the link
```

---

## Phase 6 — Update Metadata

After all notes are written:

1. **Update `topics/<topic>/_summary.md`:**
   - Update the **Key Concepts** section to list the new notes with wikilinks
   - Update the note count in the footer line
   - Add a **Gaps & Open Questions** entry if synthesis revealed coverage holes

2. **Update `_index.md`:**
   - Update the note count for this topic in the Topics table
   - Update `Total notes` in KB Statistics

3. **Append to `_log.md`:**

```markdown
## [YYYY-MM-DD] synthesise | <topic>

- Created: [list of files created, one per line]
- Updated: topics/<topic>/_summary.md, _index.md
- Note count: <N> new notes (<M> full articles, <K> stubs, <J> entities, <L> syntheses)
```

4. **Refresh the frontmatter cache:**

```bash
kb-index --update
```

This keeps `kb-search` accurate for the next session. If `kb-index` is not on PATH, skip silently.

5. **Mark checkpoint complete.**

---

## Phase 7 — Report

> **Synthesis complete for `<topic>`**
>
> **Written:**
> - Full articles: N
> - Stubs: N
> - Entities: N
> - Synthesis notes: N
>
> **Updated:** `_summary.md`, `_index.md`, `_log.md`
>
> **Next steps:**
> - Run `/kb-lint <topic>` to check for broken wikilinks, missing stubs, and contradictions
> - Run `/kb-synthesise <other-topic>` if cross-topic connections were flagged

---

## Authorship Guard (always active)

Before every write:
1. Target path must start with `knowledgebase/`
2. If file exists, read it first and verify `author: kb-bot` in frontmatter
3. Source notes are **immutable** — never modify them, only read them
4. Hard abort on any violation — report and stop

---

## Edge Cases

**Concept appears in 1 source now, but the source note references it as if it will have a full note:**
Create a stub. It will be promoted to a full article when a second source is added.

**Two concepts are closely related but distinct:**
Write two separate notes, each with a cross-link in the `## See Also` section.

**A concept is mathematical (theorem, formula, model):**
Always use LaTeX. The concept note should include the formal statement of the theorem/formula and an intuitive explanation.

**A concept note already exists as a stub:**
Promote it to a full article by rewriting it (stub→full is an exception to the append-only rule, since the stub is intended to be replaced). Update `status: draft` and expand the content. Log the promotion.

**Cross-topic canonical concept (e.g. "neural network" appears in both LLM and EA topics):**
Check if a canonical note already exists in another topic. If yes: create a lightweight note in the current topic that says "See canonical note at [[topics/<other-topic>/notes/concepts/<slug>]]." If no canonical note exists yet, write the full article in the topic where it is most central, and create a stub in the other topic that links to it.
