from __future__ import annotations

from pathlib import Path

from kb_feed.nudge import build_nudge_text, inbox_counts


def _kb(tmp_path: Path, links: str, notes: str) -> Path:
    kb_root = tmp_path / "kb"
    (kb_root / "readinglist").mkdir(parents=True)
    (kb_root / "readinglist" / "Links.md").write_text(
        "---\nauthor: kb-bot\nmodified: 2026-05-01\n---\n\n# URL Inbox\n\n" + links, encoding="utf-8"
    )
    (kb_root / "readinglist" / "topics.md").write_text(
        "---\nauthor: kb-bot\nmodified: 2026-05-01\n---\n\n# Topic Interests\n\n" + notes, encoding="utf-8"
    )
    return kb_root


def test_nudge_counts_and_text(tmp_path) -> None:
    kb_root = _kb(
        tmp_path,
        links="- [ ] https://a.example\n- [x] https://done.example\n",
        notes="- [ ] some idea\n",
    )
    assert inbox_counts(kb_root) == (1, 1)
    text = build_nudge_text(kb_root, proposals_pending=2)
    assert "1 link(s)" in text and "1 inspiration note(s)" in text and "2 unapplied proposal(s)" in text


def test_nudge_none_when_clear(tmp_path) -> None:
    kb_root = _kb(tmp_path, links="- [x] https://done.example\n", notes="")
    assert build_nudge_text(kb_root, proposals_pending=0) is None
