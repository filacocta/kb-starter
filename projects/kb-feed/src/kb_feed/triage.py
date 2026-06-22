from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kb_feed.errors import KbFeedError
from kb_feed.llm import (
    LlmClient,
    LlmConfig,
    LlmUnavailable,
    normalize_assignments,
    normalize_inspiration_proposals,
)
from kb_feed.state import StateStore, validate_topic_slug


LOGGER = logging.getLogger(__name__)

LAST_ACTIVITY_META_KEY = "last_activity_at"
LAST_LINK_TRIAGE_META_KEY = "last_link_triage_at"
LAST_INSPIRATION_TRIAGE_META_KEY = "last_inspiration_triage_at"

DEFAULT_INACTIVITY_HOURS = 2.0

UNCHECKED_LINK_RE = re.compile(r"^-\s+\[\s\]\s+(?P<url>https?://\S+)(?P<rest>.*)$")
UNCHECKED_NOTE_RE = re.compile(r"^-\s+\[\s\]\s+(?P<text>.+?)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Reserved capture lanes are never offered as triage targets.
RESERVED_TOPICS = {"unfiltered", "inspiration-capture"}


@dataclass(frozen=True)
class LinkItem:
    url: str
    rest: str

    @property
    def display(self) -> str:
        return f"{self.url}{self.rest}".rstrip()


@dataclass(frozen=True)
class InspirationItem:
    text: str


@dataclass(frozen=True)
class TriageReport:
    kind: str
    ran: bool
    proposals_path: Path | None
    item_count: int
    proposal_count: int
    skipped_reason: str | None = None


def parse_unchecked_links(path: Path) -> list[LinkItem]:
    if not path.exists():
        return []
    items: list[LinkItem] = []
    text = _strip_html_comments(path.read_text(encoding="utf-8"))
    for line in text.splitlines():
        match = UNCHECKED_LINK_RE.match(line)
        if match:
            items.append(LinkItem(url=match.group("url").strip(), rest=match.group("rest")))
    return items


def parse_unchecked_inspirations(path: Path) -> list[InspirationItem]:
    if not path.exists():
        return []
    items: list[InspirationItem] = []
    text = _strip_html_comments(path.read_text(encoding="utf-8"))
    for line in text.splitlines():
        match = UNCHECKED_NOTE_RE.match(line)
        if not match:
            continue
        text = match.group("text").strip()
        # Skip pure URLs — those belong in Links.md, not topic interests.
        if text.startswith("http://") or text.startswith("https://"):
            continue
        items.append(InspirationItem(text=text))
    return items


def _strip_html_comments(text: str) -> str:
    return HTML_COMMENT_RE.sub("", text)


def discover_topics(kb_root: Path) -> tuple[str, ...]:
    topics_dir = kb_root / "topics"
    if not topics_dir.exists():
        return ()
    slugs: list[str] = []
    for entry in sorted(topics_dir.iterdir()):
        if not entry.is_dir():
            continue
        slug = entry.name
        if slug in RESERVED_TOPICS:
            continue
        try:
            validate_topic_slug(slug)
        except KbFeedError:
            continue
        slugs.append(slug)
    return tuple(slugs)


def triage_links(
    store: StateStore,
    kb_root: Path,
    *,
    client: LlmClient,
    proposals_dir: Path,
    inactivity_hours: float = DEFAULT_INACTIVITY_HOURS,
    limit: int | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> TriageReport:
    """Propose topic assignments for unchecked items in readinglist/Links.md."""
    timestamp = now or datetime.now(timezone.utc)
    gate_reason = _inactivity_gate(store, threshold_hours=inactivity_hours, now=timestamp, force=force)
    if gate_reason is not None:
        return TriageReport(
            kind="links",
            ran=False,
            proposals_path=None,
            item_count=0,
            proposal_count=0,
            skipped_reason=gate_reason,
        )

    links_path = kb_root / "readinglist" / "Links.md"
    items = parse_unchecked_links(links_path)
    if limit is not None:
        items = items[:limit]
    if not items:
        store.set_meta(LAST_LINK_TRIAGE_META_KEY, timestamp.isoformat())
        return TriageReport(
            kind="links",
            ran=True,
            proposals_path=None,
            item_count=0,
            proposal_count=0,
            skipped_reason="no unchecked links",
        )

    topics = discover_topics(kb_root)
    if not topics:
        return TriageReport(
            kind="links",
            ran=False,
            proposals_path=None,
            item_count=len(items),
            proposal_count=0,
            skipped_reason="no KB topics discovered to assign to",
        )

    payload = client.complete_json(
        system_prompt=_LINKS_SYSTEM_PROMPT,
        user_prompt=_render_links_user_prompt(items, topics),
    )
    assignments = normalize_assignments(payload, topics)
    proposals_path = _write_links_proposal(
        proposals_dir,
        items=items,
        topics=topics,
        assignments=assignments,
        now=timestamp,
    )
    store.set_meta(LAST_LINK_TRIAGE_META_KEY, timestamp.isoformat())
    return TriageReport(
        kind="links",
        ran=True,
        proposals_path=proposals_path,
        item_count=len(items),
        proposal_count=len(assignments),
    )


def triage_inspiration(
    store: StateStore,
    kb_root: Path,
    *,
    client: LlmClient,
    proposals_dir: Path,
    limit: int | None = None,
    now: datetime | None = None,
) -> TriageReport:
    """Propose research/topic queue items from unchecked notes in readinglist/topics.md."""
    timestamp = now or datetime.now(timezone.utc)
    notes_path = kb_root / "readinglist" / "topics.md"
    items = parse_unchecked_inspirations(notes_path)
    if limit is not None:
        items = items[:limit]
    if not items:
        store.set_meta(LAST_INSPIRATION_TRIAGE_META_KEY, timestamp.isoformat())
        return TriageReport(
            kind="inspiration",
            ran=True,
            proposals_path=None,
            item_count=0,
            proposal_count=0,
            skipped_reason="no unchecked inspiration notes",
        )

    topics = discover_topics(kb_root)
    payload = client.complete_json(
        system_prompt=_INSPIRATION_SYSTEM_PROMPT,
        user_prompt=_render_inspiration_user_prompt(items, topics),
    )
    proposals = normalize_inspiration_proposals(payload)
    proposals_path = _write_inspiration_proposal(
        proposals_dir,
        items=items,
        topics=topics,
        proposals=proposals,
        now=timestamp,
    )
    store.set_meta(LAST_INSPIRATION_TRIAGE_META_KEY, timestamp.isoformat())
    return TriageReport(
        kind="inspiration",
        ran=True,
        proposals_path=proposals_path,
        item_count=len(items),
        proposal_count=len(proposals),
    )


def _inactivity_gate(
    store: StateStore,
    *,
    threshold_hours: float,
    now: datetime,
    force: bool,
) -> str | None:
    if force:
        return None
    if threshold_hours <= 0:
        return None
    raw = store.get_meta(LAST_ACTIVITY_META_KEY)
    if raw is None:
        return None
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = now - last
    threshold = timedelta(hours=threshold_hours)
    if elapsed < threshold:
        remaining = threshold - elapsed
        return (
            f"bot has been active in the last {threshold_hours:g}h "
            f"(elapsed {_format_timedelta(elapsed)}; need {_format_timedelta(remaining)} more)"
        )
    return None


def _format_timedelta(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


_LINKS_SYSTEM_PROMPT = (
    "You are a knowledge-base triage assistant for the user's personal wiki. "
    "Each input URL was captured into the global 'unfiltered' inbox and now needs to be "
    "assigned to one of the existing KB topic slugs that you will be given. "
    "You may NOT propose creating a new topic. If no existing topic is a good fit, "
    "use the literal slug 'skip'. Return strict JSON of the form "
    '{"assignments": [{"url": "...", "topic": "<slug-or-skip>", "confidence": 0.0-1.0, "rationale": "..."}]}. '
    "Use only ASCII in rationales. Do not fetch URLs; reason from the URL string and the topic list."
)


def _render_links_user_prompt(items: list[LinkItem], topics: tuple[str, ...]) -> str:
    payload = {
        "allowed_topics": list(topics),
        "instructions": (
            "Assign each URL to one of allowed_topics, or use 'skip' if no topic fits. "
            "Do not invent new topics. Do not fetch URLs."
        ),
        "urls": [item.display for item in items],
    }
    return json.dumps(payload, indent=2)


_INSPIRATION_SYSTEM_PROMPT = (
    "You are a research-planning assistant for the user's personal wiki. "
    "Each input is a free-form note captured to the 'inspiration-capture' lane. "
    "For each note, propose concrete research questions and candidate existing KB topic slugs "
    "where the resulting reading-list items could live. You may NOT create new KB topics. "
    "Return strict JSON of the form "
    '{"proposals": [{"note": "...", "research_questions": ["..."], "candidate_topics": ["<slug>"], "rationale": "..."}]}. '
    "Use only ASCII. Do not fetch URLs."
)


def _render_inspiration_user_prompt(items: list[InspirationItem], topics: tuple[str, ...]) -> str:
    payload = {
        "allowed_topics": list(topics),
        "instructions": (
            "Turn each note into concrete research questions plus candidate existing topic slugs. "
            "Do not invent new topics. Do not fetch URLs."
        ),
        "notes": [item.text for item in items],
    }
    return json.dumps(payload, indent=2)


def _write_links_proposal(
    proposals_dir: Path,
    *,
    items: list[LinkItem],
    topics: tuple[str, ...],
    assignments: list[dict],
    now: datetime,
) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"links-{now.strftime('%Y%m%d-%H%M%S')}.md"
    by_url = {entry["url"]: entry for entry in assignments}

    lines = [
        f"# kb-feed link triage — {now.isoformat()}",
        "",
        f"Proposed assignments for {len(items)} unchecked link(s) in `readinglist/Links.md`.",
        "",
        f"Allowed topics ({len(topics)}): {', '.join(topics) if topics else '(none)'}.",
        "",
        "These are proposals only. kb-feed has not modified the KB.",
        "",
        "## Proposals",
        "",
    ]
    for item in items:
        match = by_url.get(item.display) or by_url.get(item.url)
        topic = (match or {}).get("topic") or "unknown"
        confidence = (match or {}).get("confidence")
        rationale = (match or {}).get("rationale")
        lines.append(f"- url: {item.display}")
        lines.append(f"  topic: {topic}")
        if confidence is not None:
            lines.append(f"  confidence: {confidence:.2f}")
        if rationale:
            lines.append(f"  rationale: {rationale}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_inspiration_proposal(
    proposals_dir: Path,
    *,
    items: list[InspirationItem],
    topics: tuple[str, ...],
    proposals: list[dict],
    now: datetime,
) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"inspiration-{now.strftime('%Y%m%d-%H%M%S')}.md"
    by_note = {entry["note"]: entry for entry in proposals}

    lines = [
        f"# kb-feed inspiration triage — {now.isoformat()}",
        "",
        f"Proposed research items for {len(items)} unchecked inspiration note(s) in `readinglist/topics.md`.",
        "",
        f"Existing topics ({len(topics)}): {', '.join(topics) if topics else '(none)'}.",
        "",
        "These are proposals only. kb-feed has not modified the KB.",
        "",
        "## Proposals",
        "",
    ]
    for item in items:
        match = by_note.get(item.text)
        lines.append(f"- note: {item.text}")
        questions = (match or {}).get("research_questions") or []
        candidates = (match or {}).get("candidate_topics") or []
        rationale = (match or {}).get("rationale")
        if questions:
            lines.append("  research_questions:")
            for question in questions:
                lines.append(f"    - {question}")
        if candidates:
            valid = [slug for slug in candidates if slug in topics]
            unknown = [slug for slug in candidates if slug not in topics]
            if valid:
                lines.append(f"  candidate_topics: {', '.join(valid)}")
            if unknown:
                lines.append(f"  unknown_topics: {', '.join(unknown)} (kb-feed will not create new topics)")
        if rationale:
            lines.append(f"  rationale: {rationale}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def load_llm_client_or_explain() -> LlmClient:
    """Helper for CLI: return a configured client or raise LlmUnavailable with a clear message."""
    from kb_feed.llm import load_llm_config_from_env

    config: LlmConfig = load_llm_config_from_env()
    return LlmClient(config)
