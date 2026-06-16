---
author: kb-bot
created: 2026-04-10
modified: 2026-04-10
type: concept
topic: obsidian
title: "Obsidian"
description: "Obsidian's defining feature is its graph-based linking model: notes link to each other via wikilinks (`Note name`), and the application renders these links as a navigable graph."
status: draft
tags: ["obsidian", "overview", "note-taking", "pkm", "vault"]
sources: ["[[knowledgebase/topics/obsidian/sources/2026-04-10-obsidian-help-overview]]"]
related: ["[[knowledgebase/topics/obsidian/notes/concepts/obsidian-markdown]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-internal-links]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-plugins]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol]]", "[[knowledgebase/topics/obsidian/notes/concepts/obsidian-cli]]"]
---

# Obsidian

[Obsidian](https://obsidian.md/) is a local-first, Markdown-based knowledge management application. It stores all notes as plain `.md` files in a folder called a **vault** — there is no proprietary database, no lock-in, and no server dependency. The vault is just a folder on your filesystem that you own entirely.

Obsidian's defining feature is its **graph-based linking model**: notes link to each other via wikilinks (`[[Note name]]`), and the application renders these links as a navigable graph. The goal is to let knowledge accumulate and connect rather than filing into rigid hierarchies. This makes it well-suited for personal knowledge bases, research, writing, and any domain where ideas need to cross-reference each other over time.

## Core Philosophy

The three principles that distinguish Obsidian from most note-taking tools:

**Local-first.** Everything lives on your disk. No account required, no sync service needed (though Obsidian Sync is available as a paid option). Your notes work offline, forever, even if the company disappears.

**Plain text.** Notes are standard Markdown files. They can be opened, edited, searched, and processed by any tool that handles text: editors, scripts, version control systems, LLMs. The [Obsidian Flavored Markdown](notes/concepts/obsidian-markdown) extensions add capabilities without breaking plain-text portability.

**Extensible.** The plugin ecosystem covers an enormous range of use cases — from Dataview (treating the vault as a database), to Leaflet (interactive maps), to Templater (programmatic note creation), to Git integration, to AI integrations. See [[knowledgebase/topics/obsidian/notes/concepts/obsidian-plugins]].

## Vault Structure

A vault is just a folder. Obsidian adds a hidden `.obsidian/` subfolder for configuration (settings, plugins, themes, snippets). Everything else is your content. This means:

- Vaults can be managed with git
- Multiple vaults can exist independently on the same machine
- Vaults are trivially portable — zip and move them
- The [kb-bot plugin](https://github.com) (this KB's own plugin) lives inside `.obsidian/plugins/kb-bot/` within this vault

## Getting Started Path

The official help documentation organises the getting-started experience as five steps:
1. Download and install Obsidian
2. Create a vault (point to a folder)
3. Create notes (Markdown files)
4. Link notes (wikilinks, internal navigation)
5. Extend (plugins, themes, CSS)

## Premium Services

- **Obsidian Sync** — end-to-end encrypted sync across devices; paid subscription
- **Obsidian Publish** — host a public website from vault content; paid subscription
- **Web Clipper** — browser extension for capturing web content directly into a vault

## This KB's Relationship to Obsidian

This knowledge base is built on Obsidian as its storage and rendering layer. The [[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol|Obsidian URI protocol]] powers citation deep links. The [[knowledgebase/topics/obsidian/notes/concepts/obsidian-markdown|OFM]] conventions govern all note formatting. The [[knowledgebase/topics/obsidian/notes/concepts/obsidian-cli|Obsidian CLI]] is the intended interface for automation. Understanding Obsidian well is prerequisite for maintaining and extending this KB system.

## See Also

- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-markdown]] — OFM syntax reference
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-internal-links]] — wikilink system in depth
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-uri-protocol]] — the `obsidian://` deep-link protocol
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-cli]] — command-line interface
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-plugins]] — plugin ecosystem overview
- [[knowledgebase/topics/obsidian/notes/concepts/obsidian-dataview]] — Dataview: treating the vault as a queryable database
- [[knowledgebase/topics/obsidian/notes/syntheses/using-obsidian-strategies]] — use cases and strategies across different goals
