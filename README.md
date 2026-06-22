# kb-starter

A self-contained starter kit for a personal knowledge base built on [Obsidian](https://obsidian.md) and operated by LLMs or local tooling. Clone this repo to get the Obsidian plugin, KB skills, reusable workflow tools, optional Telegram capture bot, optional local workflow dashboard, and a blank-but-seeded knowledgebase ready to use.

The system is **LLM-agnostic**: all conventions are plain English and any capable LLM can operate the KB by reading `knowledgebase/_schema.md`. The included skills are written for agent-CLI workflows, but the KB itself can be queried by any model.

---

## What's included

| Folder | Contents |
|---|---|
| `plugin/` | `kb-bot` — Obsidian plugin source. Adds an in-vault ask command backed by a configured LLM API. |
| `skills/` | KB skills that operate the KB from an agent CLI. |
| `knowledgebase/` | Blank starter KB with schema, rules, shared `_tools/`, ideas backlog, and the `obsidian` topic pre-seeded. Copy this into your Obsidian vault. |
| `projects/kb-feed/` | Optional Telegram capture bot that routes mobile-shared URLs and notes into KB queues. |
| `projects/kb-workflow-dashboard/` | Optional read-only local web dashboard for KB workflow status. |

---

## Prerequisites

- [Obsidian](https://obsidian.md) (any recent version)
- An agent CLI capable of reading local files and running shell commands
- Node.js 18+ and npm (for building the plugin)
- `pdftotext` for PDF ingestion:
  ```bash
  sudo apt-get install poppler-utils   # WSL2 / Debian/Ubuntu
  brew install poppler                  # macOS
  ```

---

## Quick start

### 1. Build and install the plugin

```bash
cd plugin
npm install
npm run build          # produces main.js
```

Copy the plugin into your Obsidian vault:

```bash
mkdir -p /path/to/your/vault/.obsidian/plugins/kb-bot
cp main.js manifest.json /path/to/your/vault/.obsidian/plugins/kb-bot/
```

In Obsidian: **Settings → Community Plugins → KB Bot → Enable**. Then add your Anthropic API key in the plugin settings.

> **Vault folder name:** The plugin's authorship guard expects the KB to live at `knowledgebase/` relative to your vault root. This name is hardcoded in `plugin/src/guard.ts`. If you want a different folder name, edit `KB_ROOT` in that file before building.

### 2. Install the skills

```bash
bash skills/install.sh
```

This copies the included KB skills to `~/.claude/skills/` and creates `~/.claude/kb-config.md` with placeholder values.

### 3. Configure your KB path

Edit `~/.claude/kb-config.md`:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

- `KB_ROOT` — absolute path to the `knowledgebase/` folder inside your Obsidian vault
- `VAULT_NAME` — the name of your Obsidian vault (used in `obsidian://` deep links)

All KB skills read this file at startup. You only need to set it once.

### 4. Copy the starter KB

```bash
cp -r knowledgebase/ /path/to/your/vault/knowledgebase
```

The `knowledgebase/` folder includes:
- `_schema.md` — the full structural specification any agent reads to orient itself
- `_rules.md` — behavioural rules (authorship guard, navigation protocol, citation standards, etc.)
- `_index.md` — navigation index (starts with just the `obsidian` topic)
- Blank stubs for `_log.md`, `_checkpoint.md`, `_dive-queue.md`, `_todo.md`
- `_tools/` — shared scripts for linting, dedup, queue checkoff, derived dive-queue regeneration, media capture, tweet/video extraction, and prep checks
- `ideas/_backlog.md` — durable parking lot for broad ideas that are not yet topics or projects
- `readinglist/Links.md` — global URL inbox
- `topics/obsidian/` — pre-seeded topic documenting the Obsidian ecosystem (useful reference for any KB operating in Obsidian)
- `projects/kb-feed/` and `projects/kb-workflow-dashboard/` — KB-native project records for the optional workflow tools in this repo

### 5. Verify

In an agent CLI session, run:

```
/kb-ask
```

The skill should load, read your config, and show the opening menu.

---

## Skills reference

| Skill | What it does |
|---|---|
| `/kb-ask [question]` | Start a scoped KB session. Browse topics, query content, or ask a natural language question. Generates `obsidian://` citations. |
| `/kb-ingest <url\|path>` | Ingest a single source interactively. Fetches, discusses, assigns topic, writes source note. Full checkpoint support. |
| `/kb-queue [topic]` | Batch process a topic's reading list. Per-item approval checklist with pause/resume for overnight runs. |
| `/kb-synthesise [topic]` | Generate concept/entity/synthesis notes from accumulated sources. Presents an approval plan before writing. |
| `/kb-lint [topic]` | Health check: broken wikilinks, orphan pages, missing stubs, incomplete frontmatter, contradictions, stale sources. |
| `/kb-auto [topic]` | Headless autonomous ingestion from topic reading-list `## Auto-approved` sections. `_dive-queue.md` is generated/read-only. |

---

## Optional workflow tools

### kb-feed

`projects/kb-feed/` is a Python Telegram capture bot. It does not ingest sources. It validates messages, maps Telegram topic threads to KB topic slugs, writes queue entries, and keeps runtime state in SQLite outside the repo.

```bash
cd projects/kb-feed
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
mkdir -p ~/.config/kb-feed ~/.local/state/kb-feed
cp credentials.env.example ~/.config/kb-feed/credentials.env
cp config.example.toml ~/.config/kb-feed/kb-feed.toml
```

Read `projects/kb-feed/README.md` before enabling live writes.

### KB workflow dashboard

`projects/kb-workflow-dashboard/` is a dependency-free local web dashboard. It is read-only in the MVP.

```bash
python3 projects/kb-workflow-dashboard/server.py --kb-root /path/to/your/vault/knowledgebase
```

Open `http://127.0.0.1:8787`.

---

## Plugin settings

After enabling the plugin in Obsidian, configure it via **Settings → KB Bot**:

| Setting | Default | Notes |
|---|---|---|
| API Key | *(empty)* | Your Anthropic API key — required |
| Model | `claude-sonnet-4-6` | Any `claude-*` model ID |
| Max tokens | `1024` | Response length cap for in-vault queries |

Settings persist to `.obsidian/plugins/kb-bot/data.json` (gitignored).

---

## KB conventions

The full structural specification lives in `knowledgebase/_schema.md` and the behavioural rules in `knowledgebase/_rules.md`. Key points:

- **Authorship guard:** The agent only writes to files it created (`author: kb-bot` in frontmatter). It will never touch user-created notes.
- **Navigation:** Agents read `_index.md` → `_summary.md` → individual notes in order, loading only what is needed.
- **Page creation threshold:** Concept notes are stubs (one sentence) for 1 source; full articles (500–1,500 words) for 2+ sources.
- **Source notes are immutable** after initial write — provenance is always traceable.
- **LaTeX** throughout for mathematical content (`$...$` inline, `$$...$$` block).

---

## Adding a new topic

Run `/kb-ingest <url>` and the skill will propose a topic assignment. To create a topic manually:

1. Create `knowledgebase/topics/<your-topic>/` with subfolders `sources/`, `notes/concepts/`, `notes/entities/`, `notes/syntheses/`
2. Create `knowledgebase/topics/<your-topic>/_summary.md` (use an existing `_summary.md` as a template)
3. Add the topic to `knowledgebase/_index.md`
4. Add sources to `knowledgebase/topics/<your-topic>/reading-list.md`
5. Run `/kb-queue <your-topic>` to begin ingestion

---

## License

MIT
