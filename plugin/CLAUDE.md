# Optional Obsidian adapter

This plugin adds an in-vault question command using the Anthropic API. The
file-based KB and its agent skills work independently. Read the root README for
installation and `knowledgebase/AGENTS.md` for the selected KB task. Do not import
Personal ingestion procedures into a team wiki.

## Commands

Run `npm install` once, `npm test` for pure-function tests, and `npm run build`
for TypeScript checks and a production bundle. `npm run dev` watches source.
Reload the plugin in Obsidian after a development rebuild. `npm run lint` runs
the repository's ESLint rules. Never add provider credentials to the repository.

## Boundaries

`main.ts` re-exports `src/KbBotPlugin`. `AskModal` gathers the question and editor
selection. `api.ts` builds a prompt, calls the configured Anthropic model and
formats the returned callout. `KbBotSettingTab` edits settings. Existing saved
settings persist through Obsidian's plugin data; new installations choose a
model explicitly. There is no automatic provider fallback.

`guard.ts` owns the plugin's `knowledgebase/` containment and authorship checks.
Keep its writes through `safeWrite()`. `ingest.ts` and `topics.ts` contain note
and path utilities; they are not proof of the shared guarded publication lifecycle.
The team profile uses its repository review process instead of this writer.

Tests use Node's test runner and Obsidian stubs. They do not exercise real modals,
plugin lifecycle or a provider account. The build declares Obsidian external.
