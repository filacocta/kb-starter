# Evaluation Fixtures

Book-derived skills need practical tests. Do not judge them by whether they restate the source cleanly.

## Fixture Types

- **Trigger task:** test whether the skill activates for intended requests, explicit invocations, adjacent-but-wrong requests, and out-of-scope requests.
- **Before/after artifact:** a dashboard, chart, prompt, document, or code sample that should improve after applying the skill.
- **Critique task:** ask the agent to identify defects, explain why they matter, and propose concrete fixes.
- **Build task:** ask the agent to produce an artifact using the synthesized rules.
- **Adversarial task:** include edge cases where the source guidance should not apply.
- **Security task:** include malicious or overbroad descriptions, unsafe tool requests, missing provenance, path mismatches, and permission-sensitive actions when the skill package includes external artifacts or scripts.
- **Regression task:** rerun a known old output to verify the skill does not introduce worse defaults.

## Minimum Eval Set

For a new candidate, create at least:

- one trigger set with positive, explicit, adjacent-negative, and out-of-scope prompts
- one normal task squarely inside scope
- one critique task against a real artifact
- one out-of-scope or edge-case task
- one comparison against a baseline answer without the skill
- one package validation check covering referenced paths, support files, examples, and permission assumptions

## Pass Criteria

The skill passes only if it activates at the right time and produces more specific, source-grounded, and actionable output than the baseline without adding unsupported claims, generic advice, unsafe operations, or wrong-scope behavior.

Record:

- artifact path or source
- prompt used
- expected improvement
- actual result
- trigger result when applicable
- package validation result
- security or permission finding when applicable
- decision: keep, revise, split, defer, or reject
