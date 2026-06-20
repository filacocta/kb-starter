# KB Workflow Integration

Use this reference when the source-cluster-to-skill work is happening inside a KB that follows Brendan's starter structure.

## Design Goal

The skill should make skill creation a downstream artifact of KB work, not a side effect of reading a good source. The KB remains the source of truth for raw custody, source notes, synthesis, candidate lifecycle, eval results, and install decisions.

## Where State Lives

- **Raw custody:** `_library/source-custody-index.md` and `_library/books/` or `_library/docs/` for local files.
- **Topic queue:** `topics/<topic>/reading-list.md` for approved source candidates. Mutate existing queue lines only through shared `_tools/` checkoff commands.
- **Topic source notes:** `topics/<topic>/sources/` for one ingested source or extraction batch. Do not edit source-note bodies after creation.
- **Source catalog:** `topics/<topic>/notes/syntheses/source-catalog.md` when a long-form cluster spans several books, chapters, or extraction batches.
- **Practice model:** `topics/<topic>/notes/syntheses/<candidate>-practice-model.md` when sources have been synthesized into a repeatable review/build model.
- **Candidate note:** `topics/<topic>/notes/syntheses/<candidate>-skill-candidate.md` or `<candidate>-skill-path.md` for lifecycle state, promotion gates, package path, eval status, install status, starter safety, blockers, and next action.
- **Eval fixtures:** `topics/<topic>/notes/syntheses/<candidate>-eval-fixtures.md` for trigger tests, baseline comparisons, artifacts, expected improvement, actual results, and decisions.
- **Project tasks:** `projects/<project>/_todos.md` when the skill affects an active project or dashboard lane.

Use AI-literacy only for the reusable book-to-skill pipeline and general skill-creation methods. Domain skills should live in their owning topic.

## KB Execution Pattern

1. Read `_schema.md`, `_rules.md`, `_index.md`, the target topic `_summary.md`, and any existing source catalog/candidate/eval notes.
2. Identify the source cluster and intended agent behavior. If the behavior is vague, keep the work as synthesis.
3. Confirm source custody and ingestion state. Do not fetch new URLs or process new local files without the KB approval path.
4. Extract or use existing source notes to produce a topic-owned practice model before drafting a package.
5. Record promotion-gate state in a candidate note before creating or installing a skill.
6. Draft packages in a candidate workspace unless the user explicitly approves local install. Keep starter-safe copies separate from private or project-specific packages.
7. Validate package shape and referenced paths before any install decision.
8. Test trigger behavior and output behavior on real artifacts. Record the result in eval fixtures.
9. Update summaries, index/log, project todos, and checkpoint status after KB writes.

## Dashboard-Ready Candidate Fields

When recording candidate state, make these fields easy for a dashboard to parse or summarize:

- `candidate_name`
- `owning_topic`
- `source_cluster`
- `raw_custody_paths`
- `practice_model_note`
- `candidate_note`
- `eval_fixtures_note`
- `package_path`
- `lifecycle_state`
- `package_validation`
- `trigger_eval`
- `output_eval`
- `security_review`
- `install_state`
- `starter_state`
- `blockers`
- `next_action`

It is fine if these appear as prose or checklist rows today; the important design constraint is consistent naming so a later dashboard lane can extract them.

## Stop Conditions

Stop at source synthesis when:

- the material is mostly history, vocabulary, motivation, or background;
- several sources disagree and the disagreement has not been captured;
- the repeatable agent behavior is not concrete;
- the best durable output is a source catalog, concept note, deterministic helper script, dashboard lane, or project todo rather than a skill;
- there are no realistic trigger or output eval fixtures.

Stop before install when:

- package paths do not validate;
- the description is untested or overbroad;
- scripts, external tools, network access, or file mutation have not been reviewed;
- provenance or licensing is unclear;
- the candidate is useful locally but not starter-safe.

