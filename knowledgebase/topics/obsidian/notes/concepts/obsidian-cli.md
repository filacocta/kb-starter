---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: concept
topic: obsidian
title: "Obsidian CLI"
description: "> *Agent inference — the official CLI docs page (https://obsidian.md/help/cli) is JavaScript-rendered and could not be fetched."
status: draft
tags: ["obsidian", "cli", "automation", "headless", "terminal"]
sources: []
related: ["[[knowledgebase/topics/obsidian/notes/concepts/obsidian-overview]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-plugins]]"]
---

# Obsidian CLI

> *Agent inference — the official CLI docs page ([https://obsidian.md/help/cli](https://obsidian.md/help/cli)) is JavaScript-rendered and could not be fetched. This note is written from prior knowledge and the brief mention in the help overview. The CLI docs should be ingested directly when the page becomes accessible, or via a local copy.*

Obsidian provides an official **command-line interface (CLI)** that allows vault operations to be performed from the terminal without opening the graphical application. This is the tool referenced in this KB system's design principles ("vault operations via Obsidian CLI") as the intended automation interface.

## What the CLI Does

The Obsidian CLI exposes vault management operations at the command line:

- **Open vaults and notes** — equivalent to `obsidian://open` URI calls but from the terminal
- **Trigger commands** — run any command that is available in Obsidian's Command Palette programmatically
- **Non-interactive operations** — useful for scripting, CI, and automation contexts where you want to interact with vault content without launching the GUI

This complements the [[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol|URI protocol]] (which is designed for inter-application calls and document links) by providing a scriptable terminal interface.

## Installation

The CLI is bundled with the Obsidian desktop application on supported platforms. After installing Obsidian:

- **macOS/Linux:** The CLI executable may need to be added to `PATH` or invoked directly
- **Windows:** Available from the Obsidian installation directory
- **WSL2 (this KB's environment):** CLI operations targeting the Windows-hosted vault need to invoke the Windows Obsidian binary from WSL2, or use the URI protocol as an alternative

## Relationship to This KB System

The CLAUDE.md for the kb-bot plugin states: "Vault operations via Obsidian CLI only. No plugin chat UI." This means the intended workflow is:

1. Claude Code skills drive KB operations (ingest, synthesise, ask, queue)
2. The CLI handles any vault-level operations that require the Obsidian application (e.g., triggering plugin commands, opening notes for review)
3. The URI protocol handles deep-link citations in skill output

In practice, the Claude Code skills in this KB operate mostly on the filesystem directly (reading and writing `.md` files) and use the URI protocol for citations. The CLI becomes more important when we need to trigger Obsidian-specific features (Dataview refresh, plugin commands) headlessly.

## Stub Note

This note should be updated once the CLI documentation is successfully ingested. Key questions to answer:
- Full command reference (what commands are available)
- Authentication / vault selection at CLI level
- How to run plugin commands from CLI
- WSL2-specific invocation patterns

**Reading list:** `https://obsidian.md/help/cli` — add to reading list once accessible

## See Also

- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol]] — the URI protocol as a complement to the CLI
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-overview]] — Obsidian architecture overview
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-plugins]] — plugin commands that the CLI can trigger
