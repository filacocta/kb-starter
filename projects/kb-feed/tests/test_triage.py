from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from kb_feed.llm import LlmClient, load_llm_config_from_env
from kb_feed.state import StateStore
from kb_feed.triage import (
    LAST_ACTIVITY_META_KEY,
    LAST_INSPIRATION_TRIAGE_META_KEY,
    LAST_LINK_TRIAGE_META_KEY,
    discover_topics,
    parse_unchecked_inspirations,
    parse_unchecked_links,
    triage_inspiration,
    triage_links,
)


def _build_kb(tmp_path):
    kb_root = tmp_path / "kb"
    (kb_root / "topics" / "obsidian").mkdir(parents=True)
    (kb_root / "topics" / "rockhounding").mkdir(parents=True)
    readinglist = kb_root / "readinglist"
    readinglist.mkdir()
    (readinglist / "Links.md").write_text(
        "---\nauthor: kb-bot\n---\n"
        "# URL Inbox\n\n"
        "- [ ] https://example.com/a — (via Telegram #unfiltered, 2026-05-31)\n"
        "- [ ] https://example.com/b — manual save\n"
        "- [x] https://example.com/c — already triaged\n",
        encoding="utf-8",
    )
    (readinglist / "topics.md").write_text(
        "---\nauthor: kb-bot\n---\n"
        "# Topic Interests\n\n"
        "- [ ] gather rare minerals systematically — (via Telegram #inspiration-capture, 2026-05-31)\n"
        "- [ ] https://should-not-appear.example/ — pure URL\n",
        encoding="utf-8",
    )
    return kb_root


def _init_store(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    return store


def _client_with(payload: dict, *, captured: dict | None = None) -> LlmClient:
    def fake_transport(url, headers, body, timeout):
        if captured is not None:
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["body"] = json.loads(body.decode("utf-8"))
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    config = load_llm_config_from_env({"DEEPSEEK_API_KEY": "secret"})
    return LlmClient(config, transport=fake_transport)


def test_parse_unchecked_links_keeps_only_unchecked_urls(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    items = parse_unchecked_links(kb_root / "readinglist" / "Links.md")
    assert [item.url for item in items] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_parse_unchecked_links_ignores_html_comment_blocks(tmp_path) -> None:
    path = tmp_path / "Links.md"
    path.write_text(
        "# URL Inbox\n\n"
        "<!--\n"
        "- [ ] https://example.com/interesting-article\n"
        "- [ ] https://example.com/template-only\n"
        "-->\n"
        "- [ ] https://example.com/real — manual save\n",
        encoding="utf-8",
    )

    items = parse_unchecked_links(path)

    assert [item.display for item in items] == ["https://example.com/real — manual save"]


def test_parse_unchecked_inspirations_skips_pure_urls(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    items = parse_unchecked_inspirations(kb_root / "readinglist" / "topics.md")
    assert [item.text for item in items] == ["gather rare minerals systematically — (via Telegram #inspiration-capture, 2026-05-31)"]


def test_parse_unchecked_inspirations_ignores_html_comment_blocks(tmp_path) -> None:
    path = tmp_path / "topics.md"
    path.write_text(
        "# Topic Interests\n\n"
        "<!--\n"
        "- [ ] Fourier analysis examples\n"
        "- [ ] Medieval cartography sources\n"
        "-->\n"
        "- [ ] gather rare minerals systematically\n",
        encoding="utf-8",
    )

    items = parse_unchecked_inspirations(path)

    assert [item.text for item in items] == ["gather rare minerals systematically"]


def test_discover_topics_skips_reserved_and_invalid(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    # Add reserved + a hidden dir + an invalid slug.
    (kb_root / "topics" / "unfiltered").mkdir()
    (kb_root / "topics" / ".hidden").mkdir()
    (kb_root / "topics" / "INVALID").mkdir()

    topics = discover_topics(kb_root)

    assert topics == ("obsidian", "rockhounding")


def test_triage_links_blocked_by_recent_activity(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    store = _init_store(tmp_path)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    store.touch_activity((now - timedelta(minutes=30)).isoformat())
    client = _client_with({"assignments": []})

    report = triage_links(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        inactivity_hours=2.0,
        now=now,
    )

    assert report.ran is False
    assert "bot has been active" in (report.skipped_reason or "")
    assert report.proposals_path is None
    # No triage meta written.
    assert store.get_meta(LAST_LINK_TRIAGE_META_KEY) is None


def test_triage_links_runs_when_inactive_and_writes_proposal(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    store = _init_store(tmp_path)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    store.touch_activity((now - timedelta(hours=4)).isoformat())
    captured: dict = {}
    client = _client_with(
        {
            "assignments": [
                {"url": "https://example.com/a — (via Telegram #unfiltered, 2026-05-31)", "topic": "obsidian", "confidence": 0.8, "rationale": "ok"},
                {"url": "https://example.com/b — manual save", "topic": "rockhounding"},
            ]
        },
        captured=captured,
    )

    report = triage_links(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        inactivity_hours=2.0,
        now=now,
    )

    assert report.ran is True
    assert report.item_count == 2
    assert report.proposal_count == 2
    assert report.proposals_path is not None
    assert report.proposals_path.exists()
    body = report.proposals_path.read_text(encoding="utf-8")
    assert "https://example.com/a" in body
    assert "topic: obsidian" in body
    assert "topic: rockhounding" in body
    # Triage meta updated.
    assert store.get_meta(LAST_LINK_TRIAGE_META_KEY) == now.isoformat()
    # Prompt does not include topics not in topics/.
    sent_body = captured["body"]["messages"][1]["content"]
    parsed = json.loads(sent_body)
    assert set(parsed["allowed_topics"]) == {"obsidian", "rockhounding"}


def test_triage_links_force_bypasses_inactivity_gate(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    store = _init_store(tmp_path)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    store.touch_activity(now.isoformat())
    client = _client_with({"assignments": [{"url": "https://example.com/a", "topic": "skip"}]})

    report = triage_links(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        inactivity_hours=2.0,
        force=True,
        now=now,
    )

    assert report.ran is True


def test_triage_links_no_items_marks_meta_but_writes_no_file(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    # Strip Links.md down to no unchecked items.
    (kb_root / "readinglist" / "Links.md").write_text(
        "---\nauthor: kb-bot\n---\n# URL Inbox\n", encoding="utf-8"
    )
    store = _init_store(tmp_path)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    client = _client_with({"assignments": []})

    report = triage_links(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        inactivity_hours=2.0,
        now=now,
    )

    assert report.ran is True
    assert report.item_count == 0
    assert report.proposals_path is None
    assert store.get_meta(LAST_LINK_TRIAGE_META_KEY) == now.isoformat()


def test_triage_links_no_topics_refuses(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    # Remove topic dirs so discover_topics returns ().
    for path in (kb_root / "topics").iterdir():
        path.rmdir()
    store = _init_store(tmp_path)
    client = _client_with({"assignments": []})

    report = triage_links(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        inactivity_hours=0.0,
    )

    assert report.ran is False
    assert "no KB topics" in (report.skipped_reason or "")


def test_triage_inspiration_writes_proposal_and_filters_unknown(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    store = _init_store(tmp_path)
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    client = _client_with(
        {
            "proposals": [
                {
                    "note": "gather rare minerals systematically — (via Telegram #inspiration-capture, 2026-05-31)",
                    "research_questions": ["where to source rough samples", "what tests for authenticity"],
                    "candidate_topics": ["rockhounding", "made-up"],
                    "rationale": "fits rockhounding",
                }
            ]
        }
    )

    report = triage_inspiration(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
        now=now,
    )

    assert report.ran is True
    assert report.item_count == 1
    assert report.proposal_count == 1
    body = report.proposals_path.read_text(encoding="utf-8")
    assert "candidate_topics: rockhounding" in body
    assert "unknown_topics: made-up" in body
    assert store.get_meta(LAST_INSPIRATION_TRIAGE_META_KEY) == now.isoformat()


def test_triage_inspiration_no_items(tmp_path) -> None:
    kb_root = _build_kb(tmp_path)
    (kb_root / "readinglist" / "topics.md").write_text(
        "---\nauthor: kb-bot\n---\n# Topic Interests\n", encoding="utf-8"
    )
    store = _init_store(tmp_path)
    client = _client_with({"proposals": []})

    report = triage_inspiration(
        store,
        kb_root,
        client=client,
        proposals_dir=tmp_path / "proposals",
    )

    assert report.ran is True
    assert report.item_count == 0
    assert report.proposals_path is None
    assert store.get_meta(LAST_INSPIRATION_TRIAGE_META_KEY) is not None


def test_runtime_touches_activity_on_allowed_message(tmp_path) -> None:
    from kb_feed.runtime import process_message

    store = _init_store(tmp_path)
    store.allow_user(42)
    assert store.get_meta(LAST_ACTIVITY_META_KEY) is None

    class _Chat:
        id = -100123

    class _User:
        id = 42

    class _Message:
        chat = _Chat()
        from_user = _User()
        message_thread_id = None
        text = ""
        caption = None
        entities = ()
        caption_entities = ()

        def parse_entity(self, entity):
            return ""

    process_message(_Message(), store, allowed_chat_id=-100123)

    assert store.get_meta(LAST_ACTIVITY_META_KEY) is not None


def test_runtime_does_not_touch_activity_when_chat_mismatched(tmp_path) -> None:
    from kb_feed.runtime import process_message

    store = _init_store(tmp_path)

    class _Chat:
        id = -100999

    class _User:
        id = 42

    class _Message:
        chat = _Chat()
        from_user = _User()
        message_thread_id = None
        text = ""
        caption = None
        entities = ()
        caption_entities = ()

        def parse_entity(self, entity):
            return ""

    process_message(_Message(), store, allowed_chat_id=-100123)

    assert store.get_meta(LAST_ACTIVITY_META_KEY) is None
