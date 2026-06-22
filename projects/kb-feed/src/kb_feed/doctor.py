"""Shared kb-feed health report, used by both the `doctor` CLI and the /kbdoctor Telegram command."""
from __future__ import annotations

from pathlib import Path

from kb_feed.state import StateStore
from kb_feed.triage import discover_topics

RESERVED_LANES = ("inspiration-capture", "unfiltered")


def _counts(rows: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.status] = out.get(row.status, 0) + 1
    return out


def build_doctor_report(store: StateStore, kb_root: Path) -> str:
    kb_root = kb_root.expanduser()
    mappings = store.list_topic_mappings()
    mapped_slugs = {m.topic_slug for m in mappings}
    topics = set(discover_topics(kb_root))  # excludes reserved lanes by design

    lines: list[str] = ["== kb-feed doctor =="]

    lines.append(f"\nThread -> topic mappings ({len(mappings)}):")
    for m in sorted(mappings, key=lambda x: x.topic_slug):
        missing = "" if (m.topic_slug in topics or m.topic_slug in RESERVED_LANES) else "  <-- MISSING topic dir"
        lines.append(f"  {m.thread_id:>10}  {m.topic_slug}{missing}")
    for lane in RESERVED_LANES:
        lines.append(f"  reserved lane '{lane}': {'mapped' if lane in mapped_slugs else 'NOT mapped'}")

    blind = sorted(topics - mapped_slugs)
    lines.append(f"\nBlind topics (no Telegram thread) ({len(blind)}):")
    lines.extend(f"  {slug}" for slug in blind) if blind else lines.append("  (none)")

    dropped_missing = store.get_meta("dropped_missing_thread_total") or "0"
    quarantined_unmapped = store.get_meta("quarantined_unmapped_thread_total") or "0"
    lines.append("\nUnmapped-lane URLs:")
    lines.append(f"  missing-thread (General chat, dropped): {dropped_missing}")
    lines.append(f"  unmapped-thread (quarantined to inbox): {quarantined_unmapped}")

    lines.append("\nQueue backlog:")
    lines.append(f"  outbox: {_counts(list(store.list_outbox())) or '{}'}")
    lines.append(f"  capture_outbox: {_counts(list(store.list_capture_outbox())) or '{}'}")

    proposals_dir = store.path.parent / "proposals"
    if proposals_dir.exists():
        n = len(list(proposals_dir.glob("*.md")))
        lines.append(f"\nUnapplied triage/digest proposals: {n}")

    return "\n".join(lines)
