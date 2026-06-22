---
name: book-to-skill
description: Use when evaluating whether a book, article set, PDF, source cluster, or external skill artifact should become a reusable Codex skill; when extracting repeatable procedures from long-form sources; or when planning skill candidates, promotion gates, and eval fixtures from KB synthesis.
---

# Book To Skill

Use this skill to turn long-form source work into a tested skill candidate only when the source material justifies it. The default outcome is synthesis, not skill creation.

## Operating Rules

If working inside a KB that follows this starter structure, first read `_schema.md`, `_rules.md`, `_index.md`, and the relevant topic/project `_summary.md` files. Then read `references/kb-workflow-integration.md`. Respect the KB authorship guard, source-note immutability, checkpoint rules, and shared `_tools/` queue/checkoff scripts.

Do not create one skill per book by default. Several books on the same subject should usually become one synthesized practice model, a few task-specific skills, or no skill if the sources are mainly context.

Do not install external skill artifacts directly from a repo or Gist. Keep the original checkout intact, normalize a separate candidate copy if needed, validate package shape, compare it against KB synthesis, and make an explicit install/adapt/extract/ignore decision.

Treat skill descriptions and package metadata as part of the behavior surface. A misleading description can cause the wrong skill to be discovered or selected, even when the body looks harmless. Review external or generated descriptions for overbroad triggers, adversarial wording, unsupported claims, and hidden permission assumptions.

## Workflow

1. Define the task the skill would improve.

   State the concrete agent behavior being considered, such as "critique dashboard visual hierarchy" or "extract exchange microstructure rules from market-structure books." If the behavior is vague, keep the work as KB synthesis.

2. Inventory the source cluster.

   Record the book/article/PDF paths, URLs, editions, source type, trust level, and relevant chapters. Use a long-form library/catalog for raw files when a source spans multiple topics; ingest topic-specific excerpts or notes into the appropriate KB topic rather than duplicating the whole book under one topic.

   If using a KB, identify the source catalog, candidate note, eval-fixtures note, raw custody path, lifecycle state, and dashboard-visible blocker/next-action fields before drafting a package.

3. Extract operational material.

   Look for procedures, critique criteria, decision rules, examples, anti-patterns, checklists, taxonomies, and evaluation standards. Mark background, history, narrative, and vocabulary as context unless they change an agent's repeatable behavior.

4. Synthesize before packaging.

   Reconcile overlapping sources into one practice model. Capture contradictions, scope limits, and source-specific language. Prefer a single skill per durable practice area over many book-branded skills.

5. Apply the promotion gate.

   Read `references/promotion-gate.md`. Create a skill only when a repeatable task, invocation criteria, source-grounded procedure, package fit, trigger tests, output tests, and safety review are all present.

6. Draft the candidate package.

   Use `SKILL.md` for invocation rules and the core workflow. Put dense extracted guidance in `references/`. Put reusable examples and eval tasks in separate files. Keep paths exactly aligned with the package layout. Record provenance, compatibility assumptions, tool permissions, and lifecycle state in package notes or references when the skill format has no dedicated metadata field.

7. Test with eval fixtures.

   Read `references/evaluation-fixtures.md`. Test the candidate on real artifacts before installation or starter inclusion. Include trigger tests, with/without-skill output comparisons, out-of-scope tasks, and adversarial or permission-sensitive cases. Judge whether the output improves, not whether the skill summarizes the book well.

8. Decide the lifecycle.

   Record one of: no skill, source synthesis only, candidate skill, installed local skill, starter-safe skill, deprecated skill, or superseded-by-synthesis.

   In KB work, record this decision in the owning topic's candidate or skill-path synthesis and update summaries, project todos, index/log, and checkpoint state as required by the KB rules.

## Long-Form Sources

For books and other long-form material, separate raw custody from topic synthesis:

- Raw files belong in a library/books area with stable filenames and catalog metadata.
- Topic source notes should capture only the portions actually ingested for that topic.
- Cross-topic books should keep one catalog record and many topic links, not many copied PDFs.
- Copyright-sensitive extracts should stay minimal and citation-oriented.

Use `references/long-form-library.md` when designing or updating that storage model.

Use `references/kb-workflow-integration.md` when deciding where KB state should live or what a dashboard should surface.

## External Skill Artifacts

When reviewing a repo, Gist, or downloaded skill:

- Preserve the original checkout in scratch.
- Normalize only a separate candidate copy.
- Validate required files and referenced paths.
- Compare the candidate against source-grounded KB synthesis.
- Flag unsupported scope expansion, missing provenance, missing tests, path mismatches, and unsafe install assumptions.
- Choose install, adapt, extract ideas only, or ignore.

## Output Shape

For an investigation, return:

- source cluster and intended task
- likely durable home: synthesis, skill, helper script, dashboard lane, or no action
- promotion-gate result
- missing sources, package validation, trigger tests, output evals, or security checks
- candidate skill name only if promotion is justified
- next concrete action
