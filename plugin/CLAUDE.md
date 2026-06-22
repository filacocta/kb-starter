# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm install           # first-time setup
npm run dev           # esbuild watch mode — rebuilds main.js on every save
npm run build         # type-check (tsc) + production bundle
npm test              # run all unit tests once
npm run test:watch    # run tests in watch mode
npm run lint          # ESLint (TypeScript + obsidianmd rules)
node version-bump.mjs # sync version from package.json → manifest.json + versions.json
```

After a dev rebuild, reload the plugin in Obsidian: Settings → Community Plugins → KB Bot → toggle off/on.

## KB System

This plugin is the foundation for a broader knowledge base system. The KB lives at `knowledgebase/` in the parent vault. KB skills drive KB operations (installed to `~/.claude/skills/` via `skills/install.sh` in the repo root):

- `/kb-ask` — scoped conversational session over a topic; reads summaries first, generates `obsidian://` citations
- `/kb-ingest <url|path>` — ingests a single URL or PDF interactively; fetches, assigns topic, writes source note, updates reading list/summary/index, full checkpoint support
- `/kb-queue [topic]` — batch processes a topic's `reading-list.md`; per-item approval checklist, checkpointing, and pause/resume for overnight runs
- `/kb-synthesise [topic]` — generates concept/entity/synthesis notes from accumulated sources; applies page-creation threshold (stub for 1 source, full article for 2+)
- `/kb-lint [topic]` — health check: broken wikilinks, orphan pages, missing stubs, incomplete frontmatter, contradictions, stale sources
- `/kb-auto` — headless autonomous ingestion from topic reading-list `## Auto-approved` sections; `_dive-queue.md` is generated/read-only

The authorship guard (`src/guard.ts`) enforces that the plugin never writes to files it didn't create. All KB writes must go through `safeWrite()`. The KB folder name must be `knowledgebase/` (relative to vault root) — this is hardcoded in `guard.ts` as `KB_ROOT = "knowledgebase"`.

## Architecture

Single-command plugin that pops a modal, calls the Anthropic Messages API, and inserts the response as a callout block at the editor cursor.

```
main.ts               ← re-exports default from src/KbBotPlugin (Obsidian entry point)
src/
  types.ts            ← KbBotSettings interface
  constants.ts        ← DEFAULT_SETTINGS, ANTHROPIC_API_URL, ANTHROPIC_VERSION
  api.ts              ← pure functions: buildPrompt(), formatAsCallout(), askClaude()
  guard.ts            ← authorship guard: isKbBotFile(), isInsideKb(), safeWrite()
  ingest.ts           ← pure note-building: slugify, buildSourceNote, markReadingListItemDone, buildCheckpoint, etc.
  topics.ts           ← topic path helpers and index/summary builders
  KbBotPlugin.ts      ← Plugin subclass, registers command + ribbon icon + settings tab
  AskModal.ts         ← Modal: textarea input → askClaude() → replaceRange() at cursor
  KbBotSettingTab.ts  ← PluginSettingTab: API key, model, maxTokens
tests/
  __mocks__/obsidian.ts   ← minimal stubs for TFile, Modal, Notice, Plugin, etc.
  register.mjs            ← tsx loader setup (CJS + ESM asset stubs)
  asset-hooks.mjs         ← ESM stub loader for .png/.md
  api.test.ts             ← unit tests for buildPrompt() and formatAsCallout()
  guard.test.ts           ← unit tests for isKbBotFile() and isInsideKb()
  ingest.test.ts          ← unit tests for all ingest.ts pure functions
  topics.test.ts          ← unit tests for all topics.ts pure functions
```

**Test framework**: Node.js built-in `node:test` + `expect` library, run via `tsx`. Tests only cover pure functions; Obsidian-coupled code (modals, plugin lifecycle) is not tested.

**Build**: esbuild bundles to `main.js` (CJS, ES2018). `obsidian` is declared external — Obsidian injects it at runtime. The `tsconfig.test.json` path-aliases `obsidian` to `tests/__mocks__/obsidian.ts` so tests don't need the real SDK.

**Data flow**: Selected editor text (optional) + user question → `buildPrompt()` → `askClaude()` (POST to Anthropic API) → `formatAsCallout()` → `editor.replaceRange()` at cursor position.

**Settings** persist to `.obsidian/plugins/kb-bot/data.json` via Obsidian's `loadData()`/`saveData()`.
