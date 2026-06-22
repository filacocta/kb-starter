from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from kb_feed.errors import KbFeedError
from kb_feed.state import StateStore
from kb_feed.telegram_notify import send_telegram_message
from kb_feed.triage import InspirationItem, LinkItem, discover_topics, parse_unchecked_inspirations, parse_unchecked_links


LAST_WEEKLY_DIGEST_META_KEY = "last_weekly_digest_at"
LAST_WEEKLY_DIGEST_FILE_META_KEY = "last_weekly_digest_file"
LAST_WEEKLY_DIGEST_TEXT_META_KEY = "last_weekly_digest_text"
LAST_WEEKLY_DIGEST_BODY_META_KEY = "last_weekly_digest_body"  # full Markdown, for /kbdigest <section>

# Canonical digest sections (the prompt must emit these exact `## ` headings, in order).
DIGEST_SECTIONS = ("Summary", "Proposed moves", "Possible new topics", "Ingest priorities", "Deferred", "Suggested replies")
# Friendly /kbdigest <arg> -> section name.
DIGEST_SECTION_ALIASES = {
    "summary": "Summary",
    "moves": "Proposed moves",
    "topics": "Possible new topics",
    "new-topics": "Possible new topics",
    "priorities": "Ingest priorities",
    "ingest": "Ingest priorities",
    "deferred": "Deferred",
    "replies": "Suggested replies",
}
LAST_WEEKLY_DIGEST_REPLY_FILE_META_KEY = "last_weekly_digest_reply_file"
LAST_WEEKLY_DIGEST_REPLY_TEXT_META_KEY = "last_weekly_digest_reply_text"
WEEKLY_DIGEST_SNOOZED_UNTIL_META_KEY = "weekly_digest_snoozed_until"
WEEKLY_DIGEST_REMINDED_AT_META_KEY = "weekly_digest_reminded_at"
MAX_DIGEST_ITEM_CHARS = 1000
MAX_DIGEST_CONTEXT_CHARS = 120_000
MAX_DIGEST_DECISION_CHARS = 4000
MAX_TELEGRAM_CHARS = 3900
CLAUDE_DISALLOWED_TOOLS = (
    "Bash,Edit,Glob,Grep,LS,MultiEdit,NotebookEdit,NotebookRead,Read,Task,TodoWrite,WebFetch,WebSearch,Write"
)
CLAUDE_ENV_ALLOWLIST = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "NO_PROXY",
    "NODE_EXTRA_CA_CERTS",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "USER",
}


@dataclass(frozen=True)
class TopicLinkItem:
    topic_slug: str
    item: LinkItem


@dataclass(frozen=True)
class DigestReport:
    ran: bool
    proposals_path: Path | None
    item_count: int
    telegram_text: str
    skipped_reason: str | None = None


class DigestAgent(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class ClaudeCliDigestAgent:
    def __init__(self, command: tuple[str, ...] | None = None, timeout_seconds: int = 900) -> None:
        self._command = command or _claude_command_from_env()
        self._timeout_seconds = timeout_seconds

    def complete(self, prompt: str) -> str:
        try:
            with tempfile.TemporaryDirectory(prefix="kb-feed-claude-") as cwd:
                completed = subprocess.run(
                    [*self._command],
                    input=prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    cwd=cwd,
                    env=_claude_subprocess_env(),
                )
        except FileNotFoundError as exc:
            raise KbFeedError(f"Claude CLI not found: {self._command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KbFeedError(f"Claude CLI digest timed out after {self._timeout_seconds}s.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise KbFeedError(f"Claude CLI digest failed with exit {completed.returncode}: {detail[:500]}")
        text = completed.stdout.strip()
        if not text:
            raise KbFeedError("Claude CLI digest returned empty output.")
        return text


_DIGEST_SYSTEM_PROMPT = (
    "You are a read-only knowledge-base review assistant. You never fetch URLs, write to the KB, "
    "or create topics. You return only a concise Markdown proposal digest as instructed."
)


class LlmDigestAgent:
    """DigestAgent backed by the DeepSeek (OpenAI-compatible) LLM client."""

    def __init__(self, client: "object | None" = None) -> None:
        self._client = client

    def complete(self, prompt: str) -> str:
        from kb_feed.llm import LlmClient, load_llm_config_from_env

        client = self._client or LlmClient(load_llm_config_from_env())
        return client.complete_text(system_prompt=_DIGEST_SYSTEM_PROMPT, user_prompt=prompt)


def default_digest_agent() -> DigestAgent:
    """DeepSeek by default; fall back to the Claude CLI when DeepSeek creds are absent."""
    from kb_feed.llm import LlmUnavailable, load_llm_config_from_env

    try:
        load_llm_config_from_env()
    except LlmUnavailable:
        return ClaudeCliDigestAgent()
    return LlmDigestAgent()


def weekly_digest(
    store: StateStore,
    kb_root: Path,
    *,
    agent: DigestAgent,
    proposals_dir: Path,
    now: datetime | None = None,
) -> DigestReport:
    timestamp = now or datetime.now(timezone.utc)
    items = _collect_digest_items(kb_root)
    item_count = len(items["unfiltered_links"]) + len(items["topic_links"]) + len(items["inspiration_notes"])
    if item_count == 0:
        text = "Weekly KB digest: no unchecked links or inspiration notes found."
        store.set_meta(LAST_WEEKLY_DIGEST_META_KEY, timestamp.isoformat())
        store.set_meta(LAST_WEEKLY_DIGEST_FILE_META_KEY, "")
        store.set_meta(LAST_WEEKLY_DIGEST_TEXT_META_KEY, text)
        return DigestReport(ran=True, proposals_path=None, item_count=0, telegram_text=text, skipped_reason="no unchecked items")

    prompt = _render_weekly_digest_prompt(items, discover_topics(kb_root), kb_root=kb_root)
    agent_text = agent.complete(prompt)
    proposals_path = _write_weekly_digest(
        proposals_dir,
        items=items,
        agent_text=agent_text,
        now=timestamp,
    )
    telegram_text = _render_telegram_digest(agent_text, item_count=item_count, proposals_path=proposals_path)
    store.set_meta(LAST_WEEKLY_DIGEST_META_KEY, timestamp.isoformat())
    store.set_meta(LAST_WEEKLY_DIGEST_FILE_META_KEY, str(proposals_path))
    store.set_meta(LAST_WEEKLY_DIGEST_TEXT_META_KEY, telegram_text)
    store.set_meta(LAST_WEEKLY_DIGEST_BODY_META_KEY, agent_text)
    return DigestReport(ran=True, proposals_path=proposals_path, item_count=item_count, telegram_text=telegram_text)


def render_digest_section(store: StateStore, arg: str) -> str:
    """Return one section of the stored digest for the /kbdigest <section> command."""
    body = store.get_meta(LAST_WEEKLY_DIGEST_BODY_META_KEY)
    if not body:
        return "No weekly digest is available yet. Run /kbdigest first."
    key = " ".join(arg.split()).lower().strip()
    if not key or key in {"index", "sections"}:
        return _digest_summary_and_index(body)
    section_name = DIGEST_SECTION_ALIASES.get(key)
    if section_name is None:
        valid = ", ".join(sorted(DIGEST_SECTION_ALIASES))
        return f"Unknown digest section '{arg}'. Try one of: {valid}."
    sections = _parse_digest_sections(body)
    content = sections.get(section_name)
    if not content:
        return f"## {section_name}\n(no items)"
    return f"## {section_name}\n{content}".strip()


def _parse_digest_sections(body: str) -> dict[str, str]:
    """Split digest Markdown into {section-name: body-text} by level-2 headings."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _section_item_count(content: str) -> int:
    return sum(1 for ln in content.splitlines() if ln.lstrip().startswith("- "))


def _digest_summary_and_index(body: str) -> str:
    sections = _parse_digest_sections(body)
    summary = sections.get("Summary", "").strip()
    lines = ["Weekly KB digest"]
    if summary:
        lines.append("")
        lines.append(summary)
    index_bits = []
    for name in DIGEST_SECTIONS:
        if name == "Summary":
            continue
        count = _section_item_count(sections.get(name, ""))
        alias = next((a for a, n in DIGEST_SECTION_ALIASES.items() if n == name and "-" not in a), name.lower())
        index_bits.append(f"{name}: {count}  ->  /kbdigest {alias}")
    if index_bits:
        lines.append("")
        lines.append("Sections (fetch each with the command shown):")
        lines.extend(f"- {bit}" for bit in index_bits)
    return "\n".join(lines).strip()


def snooze_weekly_digest(store: StateStore, value: str, *, now: datetime | None = None) -> datetime:
    timestamp = now or datetime.now(timezone.utc)
    until = parse_snooze_until(value, now=timestamp)
    store.set_meta(WEEKLY_DIGEST_SNOOZED_UNTIL_META_KEY, until.isoformat())
    return until


def process_digest_reply(
    store: StateStore,
    *,
    decision_text: str,
    agent: DigestAgent,
    proposals_dir: Path,
    now: datetime | None = None,
) -> DigestReport:
    timestamp = now or datetime.now(timezone.utc)
    decision = " ".join(decision_text.split())
    if not decision:
        raise KbFeedError("Tell me what to approve, rename, defer, skip, or mark ingest-now.")
    if len(decision) > MAX_DIGEST_DECISION_CHARS:
        raise KbFeedError(f"Digest reply is too long; keep it under {MAX_DIGEST_DECISION_CHARS} characters.")

    digest_text = store.get_meta(LAST_WEEKLY_DIGEST_TEXT_META_KEY)
    digest_file = store.get_meta(LAST_WEEKLY_DIGEST_FILE_META_KEY)
    if not digest_text:
        raise KbFeedError("No weekly digest is available yet. Run /kbdigest first.")
    full_digest = _clip_text(_read_optional_digest_file(digest_file) or digest_text, MAX_DIGEST_CONTEXT_CHARS)
    prompt = _render_digest_reply_prompt(full_digest=full_digest, decision_text=decision)
    agent_text = agent.complete(prompt)
    proposals_path = _write_digest_reply(
        proposals_dir,
        decision_text=decision,
        agent_text=agent_text,
        source_digest_file=digest_file,
        now=timestamp,
    )
    telegram_text = _render_digest_reply_telegram(agent_text, proposals_path=proposals_path)
    store.set_meta(LAST_WEEKLY_DIGEST_REPLY_FILE_META_KEY, str(proposals_path))
    store.set_meta(LAST_WEEKLY_DIGEST_REPLY_TEXT_META_KEY, telegram_text)
    return DigestReport(ran=True, proposals_path=proposals_path, item_count=0, telegram_text=telegram_text)


def remind_due_digest(
    store: StateStore,
    *,
    bot_token: str,
    user_ids: tuple[int, ...],
    now: datetime | None = None,
) -> DigestReport:
    timestamp = now or datetime.now(timezone.utc)
    raw_until = store.get_meta(WEEKLY_DIGEST_SNOOZED_UNTIL_META_KEY)
    if raw_until is None or not raw_until.strip():
        return DigestReport(ran=False, proposals_path=None, item_count=0, telegram_text="", skipped_reason="no snoozed digest")
    try:
        until = datetime.fromisoformat(raw_until)
    except ValueError:
        store.set_meta(WEEKLY_DIGEST_SNOOZED_UNTIL_META_KEY, "")
        return DigestReport(ran=False, proposals_path=None, item_count=0, telegram_text="", skipped_reason="invalid snooze timestamp")
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if timestamp < until:
        return DigestReport(ran=False, proposals_path=None, item_count=0, telegram_text="", skipped_reason="snooze not due")

    text = store.get_meta(LAST_WEEKLY_DIGEST_TEXT_META_KEY)
    if not text:
        return DigestReport(ran=False, proposals_path=None, item_count=0, telegram_text="", skipped_reason="no digest text to resend")
    message = f"Reminder: weekly KB digest\n\n{text}"
    for user_id in user_ids:
        _send_split(bot_token=bot_token, chat_id=user_id, text=message)
    store.set_meta(WEEKLY_DIGEST_REMINDED_AT_META_KEY, timestamp.isoformat())
    store.set_meta(WEEKLY_DIGEST_SNOOZED_UNTIL_META_KEY, "")
    return DigestReport(ran=True, proposals_path=None, item_count=0, telegram_text=message)


def send_digest_to_users(*, bot_token: str, user_ids: tuple[int, ...], text: str) -> int:
    sent = 0
    for user_id in user_ids:
        _send_split(bot_token=bot_token, chat_id=user_id, text=text)
        sent += 1
    return sent


def split_telegram_message(text: str, limit: int = MAX_TELEGRAM_CHARS) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text]

    raw_chunks = _split_telegram_message_raw(text, limit)
    while True:
        chunk_count = len(raw_chunks)
        marker_len = max(len(f"({index}/{chunk_count}) ") for index in range(1, chunk_count + 1))
        if marker_len >= limit:
            raise ValueError("limit is too small for Telegram chunk markers")
        next_chunks = _split_telegram_message_raw(text, limit - marker_len)
        if len(next_chunks) == chunk_count:
            raw_chunks = next_chunks
            break
        raw_chunks = next_chunks

    chunk_count = len(raw_chunks)
    return [f"({index}/{chunk_count}) {chunk}" for index, chunk in enumerate(raw_chunks, start=1)]


def _split_telegram_message_raw(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        if len(text) - start <= limit:
            chunks.append(text[start:])
            break
        window = text[start : start + limit]
        split_at = window.rfind("\n\n")
        if split_at != -1:
            split_at += 2
        else:
            split_at = window.rfind("\n")
            if split_at != -1:
                split_at += 1
        if split_at <= 0:
            split_at = limit
        chunks.append(text[start : start + split_at])
        start += split_at
    return chunks


def _send_split(*, bot_token: str, chat_id: int, text: str) -> None:
    for chunk in split_telegram_message(text):
        send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=chunk)


def _read_optional_digest_file(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def parse_snooze_until(value: str, *, now: datetime | None = None) -> datetime:
    timestamp = now or datetime.now(timezone.utc)
    raw = value.strip().lower()
    if raw in {"", "later"}:
        return timestamp + timedelta(hours=3)
    if raw == "tomorrow":
        return (timestamp + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    match = re.fullmatch(r"(\d+)\s*([hmHdD])", raw)
    if not match:
        raise KbFeedError("Use /kbremind later, /kbremind 3h, /kbremind 1d, or /kbremind tomorrow.")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0:
        raise KbFeedError("Reminder delay must be positive.")
    if unit == "h":
        return timestamp + timedelta(hours=amount)
    if unit == "m":
        return timestamp + timedelta(minutes=amount)
    return timestamp + timedelta(days=amount)


def _claude_command_from_env(env: dict[str, str] | None = None) -> tuple[str, ...]:
    source = env if env is not None else dict(os.environ)
    raw = source.get("KB_FEED_CLAUDE_CMD", "").strip()
    if raw:
        return tuple(shlex.split(raw))
    command = _discover_claude_command()
    return (
        command,
        "-p",
        "--permission-mode",
        "plan",
        "--disallowedTools",
        CLAUDE_DISALLOWED_TOOLS,
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
    )


def _discover_claude_command() -> str:
    path_command = shutil.which("claude")
    if path_command:
        return path_command
    nvm_root = Path("~/.nvm/versions/node").expanduser()
    candidates = sorted(nvm_root.glob("*/bin/claude")) if nvm_root.exists() else []
    if candidates:
        return str(candidates[-1])
    return "claude"


def _claude_subprocess_env(env: dict[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else dict(os.environ)
    return {key: value for key, value in source.items() if key in CLAUDE_ENV_ALLOWLIST}


def _collect_digest_items(kb_root: Path) -> dict[str, Any]:
    unfiltered_links = parse_unchecked_links(kb_root / "readinglist" / "Links.md")
    inspiration_notes = parse_unchecked_inspirations(kb_root / "readinglist" / "topics.md")
    topic_links: list[TopicLinkItem] = []
    for topic_slug in discover_topics(kb_root):
        reading_list = kb_root / "topics" / topic_slug / "reading-list.md"
        for item in parse_unchecked_links(reading_list):
            topic_links.append(TopicLinkItem(topic_slug=topic_slug, item=item))
    return {
        "unfiltered_links": unfiltered_links,
        "topic_links": topic_links,
        "inspiration_notes": inspiration_notes,
    }


def _render_weekly_digest_prompt(items: dict[str, Any], topics: tuple[str, ...], *, kb_root: Path) -> str:
    payload = {
        "allowed_existing_topics": list(topics),
        "instructions": (
            "You are reviewing a KB queue packet. This is a proposal-only digest. "
            "Do not fetch URLs. Do not write to the KB. Do not create topics. Reason only from the queue packet below, "
            "the existing topic slugs, and KB architecture knowledge. "
            "Return concise Markdown using EXACTLY these level-2 headings, in this order and spelled exactly: "
            "'## Summary', '## Proposed moves', '## Possible new topics', '## Ingest priorities', '## Deferred', "
            "'## Suggested replies'. Under Summary write 1-3 sentences. Under every other heading put one '- ' bullet "
            "per item (or a single '- (none)' line if empty). Suggested replies should give approve/rename/defer/skip "
            "language the user can send later."
        ),
        "kb_root": str(kb_root),
        "unfiltered_links": [_clip_text(item.display, MAX_DIGEST_ITEM_CHARS) for item in items["unfiltered_links"]],
        "topic_reading_list_links": [
            {"topic": item.topic_slug, "url": _clip_text(item.item.display, MAX_DIGEST_ITEM_CHARS)}
            for item in items["topic_links"]
        ],
        "inspiration_notes": [_clip_text(item.text, MAX_DIGEST_ITEM_CHARS) for item in items["inspiration_notes"]],
    }
    return json.dumps(payload, indent=2)


def _render_digest_reply_prompt(*, full_digest: str, decision_text: str) -> str:
    payload = {
        "instructions": (
            "You are Claude Code helping the user review a kb-feed weekly digest. "
            "The user has responded with an approval/modification/defer instruction. "
            "Do not fetch URLs. Do not write to the KB. Do not create topics. "
            "Interpret the user's decision against the digest and return concise Markdown suitable for Telegram. "
            "Include: Acknowledged decisions, Items still needing clarification, and Next actions. "
            "Do not output shell commands or runnable command lines; describe validated actions in plain language. "
            "If source ingestion is requested, describe the Codex/Claude KB skill handoff instead of claiming it was done."
        ),
        "user_decision": decision_text,
        "latest_digest": full_digest,
    }
    return json.dumps(payload, indent=2)


def _clip_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n[truncated to {limit} characters]"


def _write_weekly_digest(
    proposals_dir: Path,
    *,
    items: dict[str, Any],
    agent_text: str,
    now: datetime,
) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"weekly-digest-{now.strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        f"# kb-feed weekly digest — {now.isoformat()}",
        "",
        "Generated by Claude CLI from a kb-feed queue packet.",
        "These are proposals only. kb-feed has not modified the KB, created topics, ingested sources, or fetched URLs.",
        "",
        "## Input Counts",
        "",
        f"- Unfiltered links: {len(items['unfiltered_links'])}",
        f"- Topic reading-list links: {len(items['topic_links'])}",
        f"- Inspiration notes: {len(items['inspiration_notes'])}",
        "",
        "## Agent Response",
        "",
        agent_text.strip(),
        "",
    ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_digest_reply(
    proposals_dir: Path,
    *,
    decision_text: str,
    agent_text: str,
    source_digest_file: str | None,
    now: datetime,
) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"weekly-digest-reply-{now.strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        f"# kb-feed weekly digest reply — {now.isoformat()}",
        "",
        "Generated by Claude CLI from the latest digest and the user's Telegram reply.",
        "These are proposals only. kb-feed has not modified the KB, created topics, ingested sources, or fetched URLs.",
        "",
        f"Source digest: {source_digest_file or '(stored Telegram summary only)'}",
        "",
        "## User Reply",
        "",
        decision_text,
        "",
        "## Agent Response",
        "",
        agent_text.strip(),
        "",
    ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _render_telegram_digest(agent_text: str, *, item_count: int, proposals_path: Path) -> str:
    index = _digest_summary_and_index(agent_text)
    footer = (
        f"\n\n{item_count} unchecked item(s) reviewed. Full proposal: {proposals_path}"
        f"\nCommands: /kbdigest <section>, /kbreply <decision>, /kbremind 3h"
    )
    return f"{index}{footer}".strip()


def _render_digest_reply_telegram(agent_text: str, *, proposals_path: Path) -> str:
    header = "KB digest reply\n\n"
    footer = f"\n\nFull reply: {proposals_path}\nCommands: /kbreply <decision>, /kbremind 3h, /kbdigest"
    body = agent_text.strip()
    return f"{header}{body}{footer}".strip()


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())
