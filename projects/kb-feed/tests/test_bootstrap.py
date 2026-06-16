from __future__ import annotations

from kb_feed.bootstrap import bootstrap_seen_urls
from kb_feed.state import StateStore


def test_bootstrap_seen_urls_reads_topic_lists_and_dive_queue(tmp_path) -> None:
    kb_root = tmp_path / "kb"
    topic_dir = kb_root / "topics" / "obsidian"
    topic_dir.mkdir(parents=True)
    (topic_dir / "reading-list.md").write_text(
        "# Obsidian Reading List\n\n"
        "## Queue\n"
        "- [ ] https://www.example.com/a?utm_source=x&b=2 — saved\n"
        "- [x] https://example.com/done — processed\n",
        encoding="utf-8",
    )
    (kb_root / "_dive-queue.md").write_text(
        "# Dive Queue\n\n"
        "## obsidian\n"
        "- [ ] https://example.com/a?b=2 — duplicate via dive queue\n"
        "## ai-literacy\n"
        "- [ ] https://openai.com/docs — saved\n",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    result = bootstrap_seen_urls(store, kb_root)

    assert result.scanned == 4
    assert result.inserted == 3
    assert result.duplicates == 1
    assert result.skipped == ()
    assert store.has_seen_url("https://example.com/a?b=2")
    assert store.has_seen_url("https://example.com/done")
    assert store.has_seen_url("https://openai.com/docs")


def test_bootstrap_seen_urls_reports_invalid_topic_slug(tmp_path) -> None:
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    (kb_root / "_dive-queue.md").write_text(
        "# Dive Queue\n\n"
        "## bad/topic\n"
        "- [ ] https://example.com/a — saved\n",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()

    result = bootstrap_seen_urls(store, kb_root)

    assert result.scanned == 1
    assert result.inserted == 0
    assert len(result.skipped) == 1
