# Promotion Gate

A source cluster should become a skill only when all required gates pass.

## Required Gates

- **Repeatable task:** The agent can perform a recurring task better with this material.
- **Clear trigger:** The skill has concrete invocation conditions.
- **Operational content:** The sources provide procedures, critique criteria, decision rules, examples, anti-patterns, or evaluation checks.
- **Synthesis complete:** Overlapping sources have been reconciled into a practice model.
- **Source scope known:** The candidate states what it does not cover.
- **Output type fit:** A skill is better than a plain synthesis, deterministic script, dashboard lane, or tool integration.
- **Trigger testable:** The description can be tested against intended, adjacent, explicit, and out-of-scope user requests.
- **Testable quality bar:** There are real artifacts or tasks that can show whether the skill improves output.
- **Package fit:** `SKILL.md`, `references/`, examples, and paths can be validated.
- **Security and provenance:** Source custody, external artifacts, scripts, tool permissions, network/file access, and adversarial trigger risks have been reviewed.
- **Lifecycle state:** The candidate has a recorded state, version or revision marker, eval result, and deprecation/supersession path.

## Fail Conditions

- The book is mainly background, history, vocabulary, or motivation.
- The skill would summarize the source rather than improve a concrete task.
- Several sources disagree and the contradiction is not captured.
- The source cluster is too thin for durable instructions.
- The candidate would duplicate an existing skill with only book-specific phrasing.
- There are no realistic eval fixtures.
- The trigger description is untested, overbroad, or likely to activate for adjacent wrong tasks.
- The candidate depends on scripts, tools, network access, or file mutation without a permission and safety review.
- The package cannot be validated because referenced paths, support files, examples, or assets are missing.

## Promotion Outcomes

- **No skill:** keep source notes and synthesis only.
- **Candidate skill:** draft locally and test before install.
- **Installed local skill:** useful for personal workflows but not skeleton-safe yet.
- **Starter-safe skill:** generic, provenance-clean, and useful without private KB content.
- **Helper script/tool:** deterministic behavior belongs in code instead of agent instructions.
- **Dashboard lane:** lifecycle tracking or decision support is the real durable artifact.
- **Superseded/deprecated:** retire or replace a candidate when new synthesis, eval results, or security findings invalidate it.
