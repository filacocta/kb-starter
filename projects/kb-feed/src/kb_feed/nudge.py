"""Cheap, count-only triage nudge: "N links + M notes awaiting triage" — no LLM call.

Surfaces the inbox so items dropped in #unfiltered / #inspiration-capture (and unapplied
triage proposals) don't rot unseen between weekly digests.
"""
from __future__ import annotations

from pathlib import Path

from kb_feed.triage import parse_unchecked_inspirations, parse_unchecked_links


def inbox_counts(kb_root: Path) -> tuple[int, int]:
    """Return (unchecked unfiltered links, unchecked inspiration notes)."""
    links = len(parse_unchecked_links(kb_root / "readinglist" / "Links.md"))
    notes = len(parse_unchecked_inspirations(kb_root / "readinglist" / "topics.md"))
    return links, notes


def build_nudge_text(kb_root: Path, *, proposals_pending: int = 0) -> str | None:
    """Short triage reminder, or None when there's nothing waiting."""
    links, notes = inbox_counts(kb_root)
    if not (links or notes or proposals_pending):
        return None
    parts: list[str] = []
    if links:
        parts.append(f"{links} link(s) in the global inbox")
    if notes:
        parts.append(f"{notes} inspiration note(s)")
    if proposals_pending:
        parts.append(f"{proposals_pending} unapplied proposal(s)")
    return "KB triage nudge: " + ", ".join(parts) + " awaiting triage. Run /kbdigest to review."


def count_pending_proposals(proposals_dir: Path) -> int:
    return len(list(proposals_dir.glob("*.md"))) if proposals_dir.exists() else 0
