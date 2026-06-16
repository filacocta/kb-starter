# kb-feed

Telegram supergroup ingestion daemon that routes mobile-shared URLs and short notes into a markdown KB's queues.

---

## Contents

- [Purpose](#purpose)
- [Architecture](#architecture)
- [Telegram + BotFather setup](#telegram--botfather-setup)
- [Supergroup and topic setup](#supergroup-and-topic-setup)
- [Topic-map commands](#topic-map-commands)
- [Config files and credentials](#config-files-and-credentials)
- [State database](#state-database)
- [Normal URL flow](#normal-url-flow)
- [Unfiltered lane](#unfiltered-lane)
- [Inspiration-capture lane](#inspiration-capture-lane)
- [Poller](#poller)
- [Flush service](#flush-service)
- [LLM triage](#llm-triage)
- [Review commands](#review-commands)
- [Telegram review loop examples](#telegram-review-loop-examples)
- [Systemd: install / enable / disable](#systemd-install--enable--disable)
- [Operational status and next steps](#operational-status-and-next-steps)
- [Manual testing](#manual-testing)
- [Troubleshooting and recovery](#troubleshooting-and-recovery)
- [Logs](#logs)
- [Security model](#security-model)
- [Day-to-day use](#day-to-day-use)

For implementation-history context, see `docs/DESIGN.md`.

---

## Purpose

The KB at `~/Vault/knowledgebase` is the long-lived home for everything the user reads, writes, and synthesises. Most queue items arrive while at a desk, but URLs and lightweight notes are commonly captured on a phone. kb-feed bridges that mobile capture flow:

1. Post URLs into specific topic threads inside a private Telegram supergroup.
2. Post text-only ideas in a topic thread when the idea belongs to that topic but does not have a source yet.
3. Use two special lanes (`unfiltered`, `inspiration-capture`) for unassigned URLs and broad cross-topic ideas.
4. A Python daemon routes each message by `message_thread_id` into the matching KB queue.
5. Optional weekly Claude/Codex-style agent digest reviews accumulated queues, proposes existing-topic assignments and possible new topic folders, and can DM the allowed Telegram user without modifying the KB on its own.

The aim is a calm, recoverable pipeline: nothing in the KB happens without an audit trail, every step is replayable, and the live KB stays behind explicit safety flags.

---

## Architecture

```
+----------------+      +-----------------+      +--------------+      +-----------------+
| Telegram       | ---> | kb-feed poll    | ---> | SQLite       | ---> | kb-feed outbox  |
| supergroup     |      | (long-polling)  |      | outbox /     |      | flush           |
| (topic threads)|      |                 |      | capture_outbox|     | (writes + git)  |
+----------------+      +-----------------+      +--------------+      +-----------------+
                                                                              |
                                                                              v
                                                                +--------------------------+
                                                                | KB markdown queues       |
                                                                |  topics/<slug>/reading-list.md  |
                                                                |  _dive-queue.md          |
                                                                |  readinglist/Links.md    |
                                                                |  readinglist/topics.md   |
                                                                +--------------------------+

                                                              ^
                                                              |
                                            +-----------------------------------+
                                            | kb-feed digest weekly             |
                                            | (agent CLI, proposals + DM)      |
                                            +-----------------------------------+
```

### Modules

| Module | Role |
|---|---|
| `kb_feed.urls` | Telegram entity extraction and URL normalisation |
| `kb_feed.security` | DNS/IP public-address validation |
| `kb_feed.markdown` | Authorship-guarded, idempotent appends under required headings |
| `kb_feed.config` | TOML + environment config loading |
| `kb_feed.dry_run` | Applies one Telegram update JSON file to a non-live KB fixture |
| `kb_feed.state` | SQLite schema and store: topic maps, allowed users, seen URLs, outbox, captures, runtime meta |
| `kb_feed.runtime` | Telegram message processing + long-polling wiring |
| `kb_feed.writer` | Outbox/capture replay, markdown flushing, writer lock, live-KB gate, optional debounced Git commits |
| `kb_feed.llm` | OpenAI-compatible chat client; defaults to DeepSeek v4; used only by legacy triage commands |
| `kb_feed.triage` | Legacy LLM proposal generation for unfiltered links and inspiration notes |
| `kb_feed.digest` | Weekly queue digest generation, snooze/reminder state, Telegram DM delivery |
| `kb_feed.telegram_notify` | Minimal Telegram `sendMessage` helper for timer-driven DMs |
| `kb_feed.bot` | CLI entrypoint (`kb-feed ...`) |

### Data path

1. **Intake (`poll`)** — long-polling subscribes to the bot's updates. Each message in the configured chat is filtered by allowed user, mapped via `message_thread_id`, URL-validated (scheme, DNS, public IP, normalisation), and enqueued into `outbox` (URLs) or `capture_outbox` (text notes).
2. **Flush (`outbox flush`)** — the writer claims pending rows, appends them to the matching KB markdown files, marks them written, and optionally stages and commits the touched files as `kb-bot <noreply@kb-feed.local>`.
3. **Weekly digest (`digest weekly`)** — optional. Monday around 10:00, agent CLI reviews accumulated unfiltered links, topic reading-list links, and inspiration notes using a kb-feed queue packet. Output is a markdown proposal under `~/.local/state/kb-feed/proposals/`; the timer can also DM a concise summary to allowed Telegram users. The default Claude invocation runs in plan mode with read/write/fetch tools explicitly denied, so the digest cannot modify the KB or fetch URLs.

---

## Telegram + BotFather setup

Done once per bot:

1. Open Telegram and DM `@BotFather`.
2. `/newbot`, give it a name and a username ending in `bot`. Save the token BotFather returns. Don't paste it in chat, in commits, or in the repo.
3. Disable privacy mode so the bot can read group messages: `/setprivacy` → choose the bot → `Disable`. Without this, the bot only sees commands.
4. Put the token into `~/.config/kb-feed/credentials.env`:
   ```
   TELEGRAM_BOT_TOKEN=<bot-token-from-botfather>
   ```
   `chmod 600 ~/.config/kb-feed/credentials.env`.

If you ever regenerate the token, replace it in `credentials.env`, then `systemctl --user restart kb-feed-poll.service`.

---

## Supergroup and topic setup

1. Create a private supergroup. In Telegram settings → group info → enable **Topics**. This converts the group to a forum where every message lives under a `message_thread_id`.
2. Add your bot as an admin with at least *Manage topics* and *Read messages*.
3. Create one topic per KB topic you intend to capture into (e.g. `obsidian`, `rockhounding`, `geospatial-mapping`). Also create the two special lanes: an `unfiltered` topic (anything without a topic yet) and an `inspiration-capture` topic (free-form research ideas, not URLs).
4. Find the chat ID. The easiest is to use `@RawDataBot` or read the JSON when running `kb-feed poll` once interactively — Telegram returns negative IDs starting with `-100`. Put it into `~/.config/kb-feed/kb-feed.toml`:
   ```toml
   allowed_chat_id = -1001234567890
   ```
5. Add your own Telegram user ID to `allowed_users` (or seed it later with `kb-feed user allow <id>`). Anyone not on the allowlist gets an "unauthorized" reply.
6. To learn each topic's `message_thread_id`, post a single message inside the topic and inspect the update payload. The simplest workflow is:
   - Start the poller manually (`kb-feed poll`).
   - Post `map <topic-slug>` inside the topic (e.g. `map obsidian`). The bot will reply with `No KB topic mapping exists for Telegram thread <thread_id>.` — you now have the thread ID.
   - Stop the poller and run `kb-feed topic-map set <thread_id> <topic-slug>`.

---

## Topic-map commands

Topic slugs must use lowercase letters, numbers, and hyphens, and must match an existing KB topic directory under `~/Vault/knowledgebase/topics/` (except for the two reserved slugs `unfiltered` and `inspiration-capture`).

```bash
# inspect
.venv/bin/kb-feed topic-map list

# add or update
.venv/bin/kb-feed topic-map set 123 obsidian

# remove
.venv/bin/kb-feed topic-map remove 123
```

Same shape for the allowed-user list:

```bash
.venv/bin/kb-feed user allow 123456789
.venv/bin/kb-feed user revoke 123456789
.venv/bin/kb-feed user list
```

All state CLI commands default to `~/.local/state/kb-feed/state.sqlite`. Use `--state-db <path>` to point at a fixture DB for tests.

---

## Config files and credentials

| Path | Purpose | Mode |
|---|---|---|
| `~/.config/kb-feed/kb-feed.toml` | Runtime config (chat ID, allowlist, paths, commit knobs) | 644 |
| `~/.config/kb-feed/credentials.env` | Secrets (Telegram token, optional LLM API key) | **600** |
| `~/.local/state/kb-feed/state.sqlite` | Runtime state (queues, mappings, seen URLs, meta) | 644 |
| `~/Vault/knowledgebase/.kb-write.lock` | shared fcntl lock guarding live KB markdown writes | 644 |
| `~/.local/state/kb-feed/proposals/` | LLM triage proposals (markdown) | 644 |

Example `kb-feed.toml`:

```toml
kb_root = "~/Vault/knowledgebase"
state_dir = "~/.local/state/kb-feed"
allowed_chat_id = -1001234567890
allowed_users = [123456789]
log_level = "INFO"
commit_debounce_seconds = 300
commit_max_pending = 20
git_user_name = "kb-bot"
git_user_email = "noreply@kb-feed.local"

# Disabled by default. Enable only for trusted topic lanes where a literal
# URL-first Telegram message may write directly under ## Auto-approved.
# [lanes.obsidian]
# auto_approve = true
```

Example `credentials.env`:

```
TELEGRAM_BOT_TOKEN=<bot-token-from-botfather>
# Optional. The default auto-discovers Claude and runs:
# claude -p --permission-mode plan --disallowedTools Bash,Edit,Glob,Grep,LS,MultiEdit,NotebookEdit,NotebookRead,Read,Task,TodoWrite,WebFetch,WebSearch,Write --disable-slash-commands --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence
KB_FEED_CLAUDE_CMD=
# Required only for `kb-feed triage`. Other vars default to DeepSeek v4.
DEEPSEEK_API_KEY=<optional-deepseek-api-key>
# Optional overrides:
KB_FEED_LLM_PROVIDER=deepseek
KB_FEED_LLM_API_BASE=https://api.deepseek.com
KB_FEED_LLM_MODEL=deepseek-v4-flash
KB_FEED_LLM_API_KEY=
KB_FEED_LLM_TIMEOUT=60
```

---

## State database

SQLite with WAL mode. Schema lives in `src/kb_feed/state.py`; current `SCHEMA_VERSION = 6`. Tables:

- `topic_map(thread_id → topic_slug)`
- `allowed_users(user_id)`
- `seen_urls(normalized_url, fetch_url, thread_id, topic_slug, first_seen_at, source, batch_id)` — dedup memory with bootstrap/Telegram provenance
- `queue_batches(id, chat_id, thread_id, source_message_id, confirmation_message_id, source_user_id, canceled_at)` — one undo target per captured Telegram message
- `outbox(id, thread_id, topic_slug, original_url, normalized_url, fetch_url, note, status, error, committed_at, batch_id, target_section)` — URL queue
- `capture_outbox(id, thread_id, capture_type, topic_slug, text, status, error, committed_at, batch_id)` — text queue for broad inspiration notes and topic-specific ideas
- `runtime_meta(key, value)` — small KV store for `last_kb_commit_at`, `last_activity_at`, `last_link_triage_at`, `last_inspiration_triage_at`

Initialize / migrate:

```bash
.venv/bin/kb-feed state init
```

Seed dedup memory from existing KB queues so old links don't appear as new:

```bash
.venv/bin/kb-feed state bootstrap-seen --kb-root ~/Vault/knowledgebase
```

The bootstrap is a *one-time* operation per fresh deploy. It reads every `topics/*/reading-list.md` and `_dive-queue.md`, normalises every URL, and inserts into `seen_urls`. Rerunning it is safe — duplicates are ignored.

Outbox status state machine:

```
pending  ----flush starts----> writing  ----markdown append ok----> written  ----git commit ok----> (committed_at set)
                              writing  ----markdown append fails->  failed   (retryable via `outbox retry`)
pending  ----Undo pressed----> canceled (skipped by flush)
```

`writing` is a transient claim; if the process is killed mid-flush, the next flush calls `reset_writing_outbox` to move those rows back to `pending` so replay is recoverable.

---

## Normal topic URL flow

You post a URL inside a mapped topic thread. End-to-end:

1. `poll` receives a Telegram update, checks `allowed_chat_id` and `allowed_users`.
2. `process_message` reads `message_thread_id`, looks up `topic_map`, and extracts URLs from Telegram `MessageEntity` payloads (not regex).
3. Each URL goes through `normalize_url`: scheme must be `http`/`https`; hostname is resolved via `socket.getaddrinfo`; addresses in private, loopback, link-local, carrier-NAT, or cloud-metadata ranges are rejected. Tracking params (`utm_*`, `fbclid`, etc.) are stripped. Twitter/X is normalised to `x.com` (canonical) but the `fetch_url` becomes `fxtwitter.com` for nice unfurls in dive sessions.
4. `enqueue_unseen_url` does the dedup + enqueue in one SQLite transaction. If the Telegram message includes note text around the URL, that note is preserved on the outbox row. New rows are attached to a queue batch so the confirmation's `Undo` button and reply-based `/kbundo` can cancel them before flush. Duplicates are reported with a `Already queued.` reply and never re-enqueued.
5. A flush later picks up the row, appends `- [ ] <url> — (via Telegram #<slug>, YYYY-MM-DD)` to `topics/<slug>/reading-list.md` under the `## Queue` heading, and marks the row written. If note text exists, the line becomes `- [ ] <url> — <note> — (via Telegram #<slug>, YYYY-MM-DD)`.
6. If `--commit` was passed (and either `--force-commit` or debounce rules let it through), kb-feed runs `git add` on the touched files and commits with identity `kb-bot <noreply@kb-feed.local>`. Commits include a body listing each captured outbox/capture row for audit.

Normal topic URLs do **not** automatically go into `_dive-queue.md`. They are source candidates in the topic reading list. Rule 31 in `~/Vault/knowledgebase/_rules.md` authorises this capture shape.

---

## Normal topic idea flow

If you post text without a URL inside a normal mapped topic thread, kb-feed treats it as a topic-specific idea, research question, or project seed. A flush writes it to `_dive-queue.md` under that topic:

```markdown
- [ ] <idea text> — (via Telegram #<topic>, YYYY-MM-DD)
```

This is the lane for “investigate this later” thoughts that already have a topic home. It creates an investigation target; it does not fetch anything.

---

## Unfiltered lane

`unfiltered` is a reserved capture-only slug. Map any topic thread to it when the messages there don't have a topic yet:

```bash
.venv/bin/kb-feed topic-map set <thread_id> unfiltered
```

URLs from that thread are appended to `readinglist/Links.md` (the global URL inbox), not to a topic reading list. They sit there until either:

- You manually move them into a topic's reading list, or
- `kb-feed triage links` proposes assignments and you act on the proposal.

The annotation still records provenance: `(via Telegram #unfiltered, YYYY-MM-DD)`.

---

## Inspiration-capture lane

`inspiration-capture` is the second reserved slug. Map a thread to it when you want to capture free-form ideas (not URLs):

```bash
.venv/bin/kb-feed topic-map set <thread_id> inspiration-capture
```

Behaviour differs from URL lanes: the *text body* is captured, URLs are not required, and entries land in `readinglist/topics.md` (topic interests). Each capture becomes `- [ ] <text> — (via Telegram #inspiration-capture, YYYY-MM-DD)`. If the message text is exactly `map <something>`, kb-feed treats it as a one-shot hint and does **not** enqueue.

`kb-feed triage inspiration` reads unchecked items here and proposes research questions plus candidate existing topic slugs.

---

## Poller

```bash
.venv/bin/kb-feed poll [--config ~/.config/kb-feed/kb-feed.toml] [--state-db ~/.local/state/kb-feed/state.sqlite]
```

The poller:

- Reads config, requires `TELEGRAM_BOT_TOKEN` in env (loaded from `credentials.env` by systemd, or `source`'d manually).
- Initialises the state DB if needed.
- Seeds `allowed_users` from config.
- Runs `Application.run_polling(allowed_updates=Update.ALL_TYPES)` from `python-telegram-bot`.
- Per message: filters by chat, checks allowlist, touches `last_activity_at`, then either enqueues a row, writes a trusted URL-first lane item when `auto_approve` is explicitly enabled, or sends a clear reply (`Queued N URL(s)...`, `Auto-approved N URL(s)...`, `Already queued.`, `No supported public http(s) URLs found.`, etc.).
- Queue confirmations for new URL or capture rows include an inline `Undo` button. You can also reply `/kbundo` to either the original captured message or the bot's queue confirmation. Undo cancels only rows that are still `pending`; already flushed rows stay in KB markdown and must be handled through normal KB review.
- Telegram handlers offload DNS + SQLite to a worker thread (`asyncio.to_thread`) so a slow resolver does not freeze the event loop.

By default, the poller writes only to SQLite. Trusted-lane `auto_approve` is disabled unless a lane explicitly opts in; when enabled it writes the URL under that topic reading list's `## Auto-approved` section through the same shared `.kb-write.lock` as flush and KB checkoff tools.

---

## Flush service

```bash
.venv/bin/kb-feed outbox flush --kb-root <path> [--allow-live-kb] [--commit | --force-commit] [--limit N] [--commit-debounce-seconds 300] [--commit-max-pending 20]
```

Key behaviours:

- Default `--kb-root` points at a fixture (anywhere that isn't the live KB). The command **refuses** the default live KB root (`~/Vault/knowledgebase`) unless `--allow-live-kb` is explicit.
- A nonblocking fcntl lock at `<kb-root>/.kb-write.lock` prevents concurrent kb-feed, review, and KB checkoff markdown writes from interleaving.
- `reset_writing_outbox` and `reset_writing_captures` move any stale `writing` rows back to `pending` before claiming new work.
- Markdown writes are authorship-guarded: the target file must have YAML frontmatter with `author: kb-bot` (Rule 1). If it doesn't, the row is marked `failed` with the rejection reason instead of corrupting the KB.
- `--commit` stages only the touched queue files. If the working tree already has uncommitted changes on those files, the flush refuses to commit (so manual edits aren't accidentally absorbed). Commits use `kb-bot <noreply@kb-feed.local>` via per-command Git flags — no global gitconfig changes.
- Debounce: by default a commit happens at most every `commit_debounce_seconds`, or earlier if `commit_max_pending` accumulates. `--force-commit` bypasses the debounce.

Retry failed rows after fixing the underlying issue:

```bash
.venv/bin/kb-feed outbox retry --id 42 --id 43
.venv/bin/kb-feed outbox retry         # all failed rows
```

Inspect the queue:

```bash
.venv/bin/kb-feed outbox list
.venv/bin/kb-feed outbox list --status failed
```

---

## LLM triage

The preferred agent path is a weekly digest through agent CLI, not frequent single-lane DeepSeek triage.

### Weekly digest

```bash
.venv/bin/kb-feed digest weekly --kb-root ~/Vault/knowledgebase [--proposals-dir <dir>] [--send-telegram]
```

- Reads unchecked items from `readinglist/Links.md`.
- Reads unchecked URL items from every `topics/<topic>/reading-list.md`.
- Reads unchecked text notes from `readinglist/topics.md`.
- Discovers existing KB topics by scanning `~/Vault/knowledgebase/topics/*/`.
- Sends the queue packet to agent CLI over stdin. By default, this uses plan mode with read/write/fetch tools explicitly denied, slash commands disabled, a strict empty MCP config, a temporary empty working directory, and a scrubbed environment so Telegram-sourced prompt text cannot trigger file edits, shell commands, MCP tools, web fetching, or process-list prompt exposure.
- Asks Claude to produce a concise review: existing-topic assignments, possible new topic folders, ingest priorities, and deferred/unclear items.
- Writes a human-readable proposal to `~/.local/state/kb-feed/proposals/weekly-digest-YYYYMMDD-HHMMSS.md`.
- With `--send-telegram`, DMs the concise summary to allowed Telegram users.

Manual Telegram command:

```text
/kbdigest
```

This runs the same digest from Telegram. Digest commands are accepted only in the configured group or in a private DM from an allowed user. If you send `/kbdigest` in a DM with the bot, the answer stays in that DM.

Reminder command:

```text
/kbremind 3h
/kbremind tomorrow
```

This snoozes the latest digest. The reminder timer checks every 15 minutes and re-sends the last digest when the snooze is due.

Approval / decision commands:

```text
/kbreply approve the rockhounding moves, defer the rest
/kbapprove item 1 and item 3
/kbrename proposed topic field-geology-tools to field-tools
/kbdefer unclear seismic links
/kbskip the vendor link
/kbingest the two highest-priority rockhounding links
```

These commands send your decision plus the latest saved digest back through agent CLI. The bot replies with a concise follow-up and writes `weekly-digest-reply-YYYYMMDD-HHMMSS.md`. They do not modify the KB; apply commands and KB ingest remain explicit follow-up steps.

For day-to-day use, you only need to remember:

```text
/kbdigest
/kbreply <what you want approved, changed, deferred, skipped, or ingested>
/kbremind 3h
```

The other decision commands are optional shortcuts for common `/kbreply` intents.

### Legacy lane triage

The older proposal-only commands still exist for one-off debugging or manual review:

```bash
.venv/bin/kb-feed triage links --kb-root ~/Vault/knowledgebase --inactivity-hours 2 [--limit N] [--force] [--proposals-dir <dir>]
.venv/bin/kb-feed triage inspiration --kb-root ~/Vault/knowledgebase [--limit N] [--proposals-dir <dir>]
```

These are not the recommended scheduled path anymore. They write proposal markdown only and do not modify the KB through kb-feed's writer.

### Provider / config

| Env var | Default | Notes |
|---|---|---|
| `KB_FEED_CLAUDE_CMD` | auto-discovered `claude -p --permission-mode plan --disallowedTools Bash,Edit,Glob,Grep,LS,MultiEdit,NotebookEdit,NotebookRead,Read,Task,TodoWrite,WebFetch,WebSearch,Write --disable-slash-commands --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence` | Command used for weekly digest and digest replies. Override only if systemd cannot find agent CLI or you want different safe flags. |
| `KB_FEED_LLM_PROVIDER` | `deepseek` | Any value works; non-default needs the next three. |
| `KB_FEED_LLM_API_BASE` | `https://api.deepseek.com` | OpenAI-compatible base URL |
| `KB_FEED_LLM_MODEL` | `deepseek-v4-flash` | DeepSeek V4 Flash model id |
| `KB_FEED_LLM_API_KEY` | falls back to `DEEPSEEK_API_KEY` if provider is `deepseek` | Required only for legacy `triage` commands |
| `KB_FEED_LLM_TIMEOUT` | `60` | Seconds |

Legacy triage fails gracefully when no API key is set: it prints `triage skipped: ...` and exits with status `2`. The weekly digest does not use DeepSeek or the OpenAI-compatible LLM client; it shells out to agent CLI.

### Safety guarantees

- Weekly digest/reply runs agent CLI in plan mode with read/write/fetch tools explicitly denied, slash commands disabled, and a strict empty MCP config by default. The prompt is passed on stdin, not as a command-line argument. The subprocess runs from a temporary empty directory with a scrubbed environment. Output goes to `~/.local/state/kb-feed/proposals/`; the default digest path cannot write files, run shell commands, call MCP tools, or fetch URLs.
- Digest packets truncate very long queue items, and `/kbreply` rejects very long decision messages.
- Legacy triage uses the API client directly and writes proposal files only. It is optional and disabled unless you run the commands/timers and configure an API key.
- kb-feed itself does not fetch URLs. The agent reasons from URL strings, captured notes, and the topic list only.
- Digest may propose new topic folders, but it does not create them. Topic creation remains a human/agent-reviewed KB action.

---

## Review commands

Review commands are the deliberate apply step after you inspect an inbox item or an LLM proposal. They can modify KB markdown, so the live KB root requires `--allow-live-kb`.

Assign one `readinglist/Links.md` URL to an existing topic reading list:

```bash
.venv/bin/kb-feed review assign-link \
  --kb-root ~/Vault/knowledgebase \
  --allow-live-kb \
  --url "https://example.com/article" \
  --topic rockhounding \
  --reason "short note explaining why it belongs here"
```

This appends the URL to `topics/<topic>/reading-list.md` and marks the source inbox line checked. Add `--dive` only when the URL is also an intentional investigation target for `_dive-queue.md`.

Promote one broad inspiration note into a topic dive queue:

```bash
.venv/bin/kb-feed review promote-inspiration \
  --kb-root ~/Vault/knowledgebase \
  --allow-live-kb \
  --contains "unique phrase from the captured note" \
  --topic rockhounding \
  --idea "Rewritten research question or project seed"
```

This appends a topic-specific item to `_dive-queue.md` and marks the source `readinglist/topics.md` note checked. It does not fetch sources or create new topics.

---

## Telegram review loop examples

### Manual digest

```text
You:
/kbdigest
```

```text
Bot:
Building weekly KB digest...
```

```text
Bot:
Weekly KB digest
14 unchecked item(s) reviewed by Claude.

Summary
- Several unfiltered links cluster around field geology tools.
- Two links fit existing rockhounding.
- One inspiration note suggests a possible new topic around portable field workflows.

Proposed existing-topic moves
1. https://example.com/mineral-kit -> rockhounding
2. https://example.com/lidar-field-notes -> geospatial-mapping

Possible new topics
- field-geology-tools

Commands: /kbremind 3h, /kbremind tomorrow, /kbdigest
```

### Snooze if busy

```text
You:
/kbremind 3h
```

```text
Bot:
Okay, I will remind you about the latest weekly KB digest at 2026-06-01T17:30:00-04:00.
```

### Approve or modify with one command

```text
You:
/kbreply approve the rockhounding move, skip the vendor ad, defer the proposed new topic until there are at least 3 more similar links
```

```text
Bot:
Sending your digest reply to Claude...
```

```text
Bot:
KB digest reply

Acknowledged decisions
- Move mineral-kit to rockhounding.
- Skip the vendor ad.
- Defer field-geology-tools until more links accumulate.

Next actions
- Apply the approved mineral-kit move to the rockhounding reading list.
- Leave the proposed field-geology-tools topic deferred for now.

Full reply: ~/.local/state/kb-feed/proposals/weekly-digest-reply-20260601-173010.md
```

### Optional shortcuts

These are equivalent to more explicit `/kbreply ...` messages:

```text
/kbapprove item 1 and item 3
/kbrename field-geology-tools to field-tools
/kbdefer unclear seismic links
/kbskip the vendor link
/kbingest the two highest-priority rockhounding links
```

The shortcuts do not make the bot more powerful; they just reduce typing on mobile. The bot still routes the decision through Claude and still does not mutate the KB automatically.

---

## Systemd: install / enable / disable

User templates live under `deploy/systemd/`:

| Unit | Purpose |
|---|---|
| `kb-feed-poll.service` | Long-polling Telegram intake daemon |
| `kb-feed-flush.service` | Oneshot live-KB flush + commit |
| `kb-feed-flush.timer` | Fires `kb-feed-flush.service` every 2 minutes |
| `kb-feed-digest-weekly.service` | Oneshot weekly LLM digest + optional Telegram DM |
| `kb-feed-digest-weekly.timer` | Fires Monday at 10:00 |
| `kb-feed-digest-reminder.service` | Resends a snoozed digest when due |
| `kb-feed-digest-reminder.timer` | Checks every 15 minutes |
| `kb-feed-triage-links.*` | Legacy one-lane link triage |
| `kb-feed-triage-inspiration.*` | Legacy one-lane inspiration triage |

Install:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/kb-feed-*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
```

Enable the live ingestion path **only after** a successful manual flush against the live KB:

```bash
systemctl --user enable --now kb-feed-poll.service
systemctl --user enable --now kb-feed-flush.timer
```

Optional weekly digest path (no live KB writes; requires working agent CLI auth):

```bash
systemctl --user enable --now kb-feed-digest-weekly.timer
systemctl --user enable --now kb-feed-digest-reminder.timer
```

Disable / stop:

```bash
systemctl --user disable --now kb-feed-poll.service
systemctl --user disable --now kb-feed-flush.timer
systemctl --user disable --now kb-feed-digest-weekly.timer
systemctl --user disable --now kb-feed-digest-reminder.timer
```

Status:

```bash
systemctl --user status kb-feed-poll.service
systemctl --user list-timers 'kb-feed-*'
```

To allow systemd user units to keep running after logout (useful on the laptop):

```bash
loginctl enable-linger $USER
```

---

## Operational status and next steps

The capture path is operational when:

- `kb-feed-poll.service` is active.
- `kb-feed-flush.timer` is active.
- Topic mappings exist for normal topics plus `unfiltered` and `inspiration-capture`.
- `~/.config/kb-feed/credentials.env` contains `TELEGRAM_BOT_TOKEN`.
- The KB queue files have `author: kb-bot` frontmatter and the expected headings.

The optional weekly review path is operational when:

- `kb-feed-digest-weekly.timer` is active; it runs Monday at 10:00 local time.
- `kb-feed-digest-reminder.timer` is active; it checks snoozed reminders every 15 minutes.
- agent CLI is authenticated and visible either through `PATH`, an auto-discovered `~/.nvm/versions/node/*/bin/claude`, or `KB_FEED_CLAUDE_CMD`.
- The Claude command keeps the proposal-only safety flags unless you intentionally override them.
- You have opened a DM with the bot at least once, so Telegram allows the bot to DM you.

Current intentional boundaries:

- kb-feed captures, flushes, proposes, and records review decisions.
- kb-feed does not fetch URLs.
- kb-feed does not create new topic folders.
- kb-feed does not ingest sources into KB source/concept notes.
- Digest replies produce follow-up proposals and commands; they do not apply changes automatically.

Known next steps:

- Add direct application of approved existing-topic moves after the reply format is stable.
- Design a safe handoff from approved `ingest-now` items to Codex/Claude KB ingest skills.
- Add exact-URL duplicate replies and a Git conflict retry path.
- Decide whether legacy one-lane DeepSeek triage should remain as a debugging tool or be removed.

---

## Manual testing

A dry-run path applies one Telegram update JSON file to an explicit non-live KB fixture. The command refuses the default live KB root.

```bash
.venv/bin/kb-feed dry-run sample-update.json --kb-root /tmp/kb-fixture --map 123=obsidian
```

End-to-end smoke test against a fixture KB:

```bash
# 1. Create a writable fixture KB.
mkdir -p /tmp/kb-fixture/topics/obsidian /tmp/kb-fixture/readinglist
cat <<'EOF' > /tmp/kb-fixture/topics/obsidian/reading-list.md
---
author: kb-bot
modified: 2026-05-31
type: meta
---
# Obsidian Reading List

## Queue
EOF
cat <<'EOF' > /tmp/kb-fixture/_dive-queue.md
---
author: kb-bot
modified: 2026-05-31
type: meta
---
# Dive Queue

## obsidian
EOF

# 2. Map a thread and post one URL via the poller, then flush.
.venv/bin/kb-feed state init --state-db /tmp/kb-fixture-state.sqlite
.venv/bin/kb-feed topic-map set --state-db /tmp/kb-fixture-state.sqlite 1 obsidian
.venv/bin/kb-feed outbox flush --state-db /tmp/kb-fixture-state.sqlite --kb-root /tmp/kb-fixture
```

The first live-KB run should always be:

```bash
.venv/bin/kb-feed outbox flush \
  --kb-root ~/Vault/knowledgebase \
  --allow-live-kb --commit --commit-debounce-seconds 0
```

Inspect the resulting git diff, then enable the timer.

---

## Troubleshooting and recovery

| Symptom | Likely cause | Recovery |
|---|---|---|
| Bot replies "Already queued." | URL is already in `seen_urls` | Expected. If wrong, delete the seen row: `sqlite3 ~/.local/state/kb-feed/state.sqlite "DELETE FROM seen_urls WHERE normalized_url='...';"` |
| Bot replies "That item was already written to the KB; queue undo cannot remove it." | `/kbundo` or the inline button targeted a batch after the flush timer wrote it | Review/remove the exact KB queue line manually if needed; first-pass undo only cancels pending SQLite rows. |
| Bot replies "This Telegram user cannot undo that queue batch." | An allowed user tried to undo another user's batch | Have the original source user press Undo or reply `/kbundo`. |
| Bot replies "This Telegram user is not allowed for kb-feed." | User not in `allowed_users` | `kb-feed user allow <id>` |
| Bot replies "No KB topic mapping exists for Telegram thread N." | Missing `topic_map` row | `kb-feed topic-map set N <slug>` |
| Flush reports `failed=N` rows | Markdown append rejected (often missing `author: kb-bot` frontmatter or required heading) | Fix the target file, then `kb-feed outbox retry` |
| Flush says "target files have unstaged changes" | Manual edits on a queue file aren't yet committed | Commit or stash those edits, then re-run flush |
| Flush/review refuses with "Another KB markdown writer is already running" | Lock at `<kb-root>/.kb-write.lock` is held | Check for active kb-feed or KB checkoff processes; remove the lock only if no process holds it |
| Poller hangs / no replies | Telegram token wrong, privacy mode on, or bot not in chat | Check `journalctl --user-unit kb-feed-poll.service -e`, re-disable privacy via BotFather, re-add bot to chat |
| `kb-feed: error: TELEGRAM_BOT_TOKEN is required` | `credentials.env` missing or not loaded | `source ~/.config/kb-feed/credentials.env` (or rely on systemd `EnvironmentFile=`) |
| `triage skipped`: No LLM API key configured | `DEEPSEEK_API_KEY` (or `KB_FEED_LLM_API_KEY`) missing | Add only if using legacy triage. The weekly digest uses agent CLI instead. |
| Weekly digest fails with "agent CLI not found" | `claude` is not on the systemd PATH and no `~/.nvm/versions/node/*/bin/claude` exists | Set `KB_FEED_CLAUDE_CMD` in `~/.config/kb-feed/credentials.env` to the full safe command, e.g. `/path/to/claude -p --permission-mode plan --disallowedTools Bash,Edit,Glob,Grep,LS,MultiEdit,NotebookEdit,NotebookRead,Read,Task,TodoWrite,WebFetch,WebSearch,Write --disable-slash-commands --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence` |
| Weekly digest DM never arrives | Bot has not had a DM opened with that user, token is wrong, or no allowed users exist in state/config | Send `/start` to the bot in Telegram, check `kb-feed user list`, then inspect `journalctl --user-unit kb-feed-digest-weekly.service -e` |
| Digest proposes a topic that does not exist | Expected | It is only a proposal. Create the topic through normal KB workflow if the cluster is worth promoting. |

If the state DB ever gets into a confusing state, the safe reset is:

1. Stop services (`systemctl --user stop kb-feed-poll.service kb-feed-flush.timer`).
2. Back up the DB: `cp ~/.local/state/kb-feed/state.sqlite ~/.local/state/kb-feed/state.sqlite.bak`.
3. Re-bootstrap: `kb-feed state init` (idempotent) then `kb-feed state bootstrap-seen --kb-root ~/Vault/knowledgebase`.
4. Re-seed `allowed_users` and `topic_map` from config / manual records.

---

## Logs

- **Poller**: `journalctl --user-unit kb-feed-poll.service -f` (live tail). Set `log_level = "DEBUG"` in `kb-feed.toml` to see message-level processing.
- **Flush**: `journalctl --user-unit kb-feed-flush.service -e` (most recent run).
- **Digest**: `journalctl --user-unit kb-feed-digest-weekly.service -e` and `journalctl --user-unit kb-feed-digest-reminder.service -e`. Proposal files themselves live in `~/.local/state/kb-feed/proposals/`.
- **Timers**: `systemctl --user list-timers 'kb-feed-*'` shows next/last fire times.
- **Audit**: the KB's git history is the long-term audit log of what kb-feed wrote. Filter with `git -C ~/Vault/knowledgebase log --author=kb-bot`.

---

## Security model

- **Secrets** never enter the repo. `credentials.env` is `chmod 600` and loaded by systemd via `EnvironmentFile=`.
- **URL safety** rejects non-http(s) schemes and DNS responses pointing at private, loopback, link-local, carrier-NAT (`100.64.0.0/10`), or cloud-metadata (`169.254.169.254`) addresses. Dry-run uses the same validation path unless tests inject a resolver.
- **KB authorship guard** (Rule 1): markdown appends fail if the target file lacks `author: kb-bot` in frontmatter. This is the non-negotiable backstop against kb-feed writing to files the user owns by hand.
- **Allowlists** are explicit. State-changing capture requires both `allowed_chat_id` (one chat) and `allowed_users` (per-user IDs). Digest commands are accepted only from that group or from a private DM whose chat ID matches an allowed user.
- **Live KB gate**: every command that could touch `~/Vault/knowledgebase` refuses unless `--allow-live-kb` is set explicitly.
- **Git identity**: `kb-bot <noreply@kb-feed.local>` is passed per-command (`git -c user.name=... -c user.email=...`). The global gitconfig is never modified.
- **No URL fetching** anywhere in kb-feed. Capture, triage, and digest reason from URL strings and captured notes only.

---

## Day-to-day use

1. Post a URL inside the matching topic thread. The bot replies in seconds with `Queued 1 URL(s) for <topic>.` plus an `Undo` button (or `Already queued.`).
2. If you pasted into the wrong thread, press `Undo`, reply `/kbundo` to your original message, or reply `/kbundo` to the bot's queue confirmation before the flush timer writes it.
3. The flush timer (every 2 minutes) writes pending queue rows to KB markdown, commits, and pushes nothing — you push the KB repo manually when you want.
4. Post a text-only idea inside a matching topic thread when the idea already belongs to that topic. It lands in `_dive-queue.md`.
5. For half-baked URLs you haven't classified, post them in the `unfiltered` topic.
6. For broad cross-topic thoughts or ideas without a clear KB home, post them in the `inspiration-capture` topic.
7. Monday around 10:00, the weekly digest proposes existing-topic assignments, possible new topic folders, ingest priorities, and deferred items. Use `/kbremind 3h` or `/kbremind tomorrow` if you are busy, or `/kbdigest` to run it manually.
8. Reply with `/kbapprove`, `/kbrename`, `/kbdefer`, `/kbskip`, `/kbingest`, or free-form `/kbreply ...`; the reply is sent back through Claude with the latest digest.
9. Apply accepted queue moves with `kb-feed review assign-link` or `kb-feed review promote-inspiration`; source ingestion still happens through the normal KB agent workflow.
10. Check `git -C ~/Vault/knowledgebase log --author=kb-bot --since=yesterday` if you want to see what kb-feed added.

The contract: kb-feed only modifies KB queue files (`topics/*/reading-list.md`, `_dive-queue.md`, `readinglist/Links.md`, `readinglist/topics.md`) with the `kb-bot` author guard. Anything else is yours.
