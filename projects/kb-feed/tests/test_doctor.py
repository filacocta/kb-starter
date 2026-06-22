from __future__ import annotations

from pathlib import Path

from kb_feed.doctor import build_doctor_report
from kb_feed.state import StateStore


def _kb(tmp_path: Path) -> Path:
    kb_root = tmp_path / "kb"
    (kb_root / "topics" / "obsidian").mkdir(parents=True)
    (kb_root / "topics" / "rockhounding").mkdir(parents=True)
    return kb_root


def test_doctor_report_lists_mappings_blind_topics_and_drops(tmp_path) -> None:
    kb_root = _kb(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.set_topic_mapping(7, "obsidian")
    store.increment_meta("quarantined_unmapped_thread_total", 3)

    report = build_doctor_report(store, kb_root)

    assert "7  obsidian" in report
    # rockhounding has a topic dir but no thread mapping -> blind
    assert "rockhounding" in report
    assert "Blind topics" in report
    assert "unmapped-thread (quarantined to inbox): 3" in report
    assert "reserved lane 'unfiltered': NOT mapped" in report
