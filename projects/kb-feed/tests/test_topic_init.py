from __future__ import annotations

from datetime import date
from pathlib import Path

from kb_feed.topic_init import create_topic_skeleton


def _kb_with_index(tmp_path: Path) -> Path:
    kb_root = tmp_path / "kb"
    (kb_root / "topics").mkdir(parents=True)
    (kb_root / "_index.md").write_text(
        "---\nauthor: kb-bot\n---\n\n"
        "# Knowledge Base Index\n\n"
        "## Topics\n\n"
        "| Topic | Summary | Notes | Sources | Last updated |\n"
        "|---|---|---|---|---|\n"
        "| [[knowledgebase/topics/obsidian|obsidian]] | [[knowledgebase/topics/obsidian/_summary]] | 1 | 2 | 2026-05-20 |\n"
        "\n---\n\n## KB Statistics\n\n- Topics: 1\n- Projects: 0\n",
        encoding="utf-8",
    )
    return kb_root


def test_create_topic_skeleton_writes_ladder_and_files(tmp_path) -> None:
    kb_root = _kb_with_index(tmp_path)

    created = create_topic_skeleton(kb_root, "new-topic", today=date(2026, 6, 21))
    assert created is True

    topic = kb_root / "topics" / "new-topic"
    rl = (topic / "reading-list.md").read_text(encoding="utf-8")
    assert "author: kb-bot" in rl
    for section in ("## Pending", "## Approved", "## Auto-approved", "## Processed", "## Failed — Retry"):
        assert section in rl
    summary = (topic / "_summary.md").read_text(encoding="utf-8")
    assert "type: summary" in summary and "topic: new-topic" in summary
    ideas = (topic / "research-ideas.md").read_text(encoding="utf-8")
    assert "## Ideas" in ideas
    for sub in ("sources", "notes/concepts", "notes/entities", "notes/media", "notes/syntheses"):
        assert (topic / sub).is_dir()


def test_create_topic_skeleton_updates_index(tmp_path) -> None:
    kb_root = _kb_with_index(tmp_path)
    create_topic_skeleton(kb_root, "new-topic", today=date(2026, 6, 21))

    index = (kb_root / "_index.md").read_text(encoding="utf-8")
    assert "[[knowledgebase/topics/new-topic|new-topic]]" in index
    assert "- Topics: 2" in index  # bumped from 1


def test_create_topic_skeleton_is_idempotent(tmp_path) -> None:
    kb_root = _kb_with_index(tmp_path)
    assert create_topic_skeleton(kb_root, "new-topic") is True
    assert create_topic_skeleton(kb_root, "new-topic") is False
