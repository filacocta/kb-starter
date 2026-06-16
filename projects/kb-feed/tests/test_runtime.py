from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import kb_feed.runtime as runtime
from kb_feed.runtime import (
    _is_allowed_command_chat,
    handle_kbdigest,
    handle_kbremind,
    handle_kbreply,
    process_message,
    process_undo_callback,
    process_undo_reply,
)
from kb_feed.state import StateStore


@dataclass(frozen=True)
class FakeUser:
    id: int


@dataclass(frozen=True)
class FakeChat:
    id: int
    type: str = "supergroup"


@dataclass(frozen=True)
class FakeEntity:
    type: str
    offset: int = 0
    length: int = 0
    url: str | None = None


@dataclass
class FakeMessage:
    chat: FakeChat
    from_user: FakeUser | None
    message_thread_id: int | None
    text: str | None
    caption: str | None = None
    entities: list[FakeEntity] | None = None
    caption_entities: list[FakeEntity] | None = None
    message_id: int = 1
    reply_to_message: FakeMessage | None = None
    replies: list[str] | None = None

    def parse_entity(self, entity: FakeEntity) -> str:
        source = self.text or self.caption or ""
        return source[entity.offset : entity.offset + entity.length]

    async def reply_text(self, text: str, **_kwargs) -> None:
        if self.replies is None:
            self.replies = []
        self.replies.append(text)


def _resolver(address: str):
    def resolve(*_args):
        return [(None, None, None, None, (address, 0))]

    return resolve


def _store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.allow_user(111)
    store.set_topic_mapping(123, "obsidian")
    return store


def _kb_fixture(tmp_path):
    kb_root = tmp_path / "kb"
    topic_dir = kb_root / "topics" / "obsidian"
    topic_dir.mkdir(parents=True)
    (topic_dir / "reading-list.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "---\n\n"
        "# Obsidian Reading List\n\n"
        "## Queue\n",
        encoding="utf-8",
    )
    return kb_root


def test_process_message_ignores_wrong_chat(tmp_path) -> None:
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100999),
            from_user=FakeUser(111),
            message_thread_id=123,
            text="https://example.com/a",
            entities=[FakeEntity(type="url", offset=0, length=21)],
        ),
        _store(tmp_path),
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "ignored_chat"


def test_process_message_rejects_unauthorized_user(tmp_path) -> None:
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(999),
            message_thread_id=123,
            text="https://example.com/a",
            entities=[FakeEntity(type="url", offset=0, length=21)],
        ),
        _store(tmp_path),
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "unauthorized_user"
    assert result.reply_text == "This Telegram user is not allowed for kb-feed."


def test_process_message_replies_for_unmapped_thread(tmp_path) -> None:
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=999,
            text="https://example.com/a",
            entities=[FakeEntity(type="url", offset=0, length=21)],
        ),
        _store(tmp_path),
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "unmapped_thread"
    assert result.reply_text == "No KB topic mapping exists for Telegram thread 999."


def test_process_message_ignores_general_chat_without_urls(tmp_path) -> None:
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=None,
            text="normal discussion, no links",
            entities=[],
        ),
        _store(tmp_path),
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "no_urls"
    assert result.reply_text is None


def test_process_message_queues_url_entity_into_outbox(tmp_path) -> None:
    store = _store(tmp_path)
    text = "https://www.example.com/a?utm_source=t&b=2"

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text=text,
            entities=[FakeEntity(type="url", offset=0, length=len(text))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    rows = store.list_outbox()
    assert result.status == "queued"
    assert result.outbox_ids == (1,)
    assert result.batch_id == 1
    assert rows[0].topic_slug == "obsidian"
    assert rows[0].fetch_url == "https://example.com/a?b=2"
    assert rows[0].note is None
    assert rows[0].batch_id == 1
    assert rows[0].target_section == "pending"


def test_process_message_auto_approve_default_off_even_with_kb_root(tmp_path) -> None:
    store = _store(tmp_path)
    kb_root = _kb_fixture(tmp_path)
    text = "https://example.com/a"

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text=text,
            entities=[FakeEntity(type="url", offset=0, length=len(text))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
        kb_root=kb_root,
    )

    rows = store.list_outbox()
    assert result.status == "queued"
    assert result.outbox_ids == (1,)
    assert result.auto_approved_ids == ()
    assert rows[0].status == "pending"
    assert rows[0].target_section == "pending"
    assert "https://example.com/a" not in (
        kb_root / "topics" / "obsidian" / "reading-list.md"
    ).read_text(encoding="utf-8")


def test_process_message_auto_approves_url_first_trusted_lane(tmp_path) -> None:
    store = _store(tmp_path)
    kb_root = _kb_fixture(tmp_path)
    text = "https://example.com/a save this"

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text=text,
            entities=[FakeEntity(type="url", offset=0, length=len("https://example.com/a"))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
        kb_root=kb_root,
        auto_approve_lanes=("obsidian",),
    )

    rows = store.list_outbox()
    reading_list = (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    line = "- [ ] https://example.com/a — save this — (via Telegram #obsidian,"
    assert result.status == "auto_approved"
    assert result.reply_text == "Auto-approved 1 URL(s) for obsidian."
    assert result.outbox_ids == ()
    assert result.auto_approved_ids == (1,)
    assert rows[0].status == "written"
    assert rows[0].target_section == "auto-approved"
    assert reading_list.index("## Auto-approved") < reading_list.index(line) < reading_list.index("## Queue")


def test_process_message_trusted_lane_queues_when_url_is_not_first(tmp_path) -> None:
    store = _store(tmp_path)
    kb_root = _kb_fixture(tmp_path)
    text = "Read https://example.com/a"

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text=text,
            entities=[FakeEntity(type="url", offset=len("Read "), length=len("https://example.com/a"))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
        kb_root=kb_root,
        auto_approve_lanes=("obsidian",),
    )

    rows = store.list_outbox()
    assert result.status == "queued"
    assert rows[0].status == "pending"
    assert rows[0].target_section == "pending"
    assert "## Auto-approved" not in (
        kb_root / "topics" / "obsidian" / "reading-list.md"
    ).read_text(encoding="utf-8")


def test_process_message_preserves_note_around_url(tmp_path) -> None:
    store = _store(tmp_path)
    url = "https://www.example.com/a?utm_source=t"
    text = f"Research this {url} later"

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text=text,
            entities=[FakeEntity(type="url", offset=len("Research this "), length=len(url))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    rows = store.list_outbox()
    assert result.status == "queued"
    assert rows[0].note == "Research this later"


def test_process_message_captures_topic_text_without_url(tmp_path) -> None:
    store = _store(tmp_path)

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text="Investigate whether Dataview can make a queue dashboard.",
            entities=[],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    captures = store.list_capture_outbox()
    assert result.status == "captured"
    assert result.reply_text == "Captured topic idea for obsidian."
    assert result.batch_id == 1
    assert captures[0].capture_type == "topic-idea"
    assert captures[0].topic_slug == "obsidian"
    assert captures[0].text == "Investigate whether Dataview can make a queue dashboard."
    assert store.list_outbox() == ()


def test_process_message_ignores_unknown_commands_in_topic_threads(tmp_path) -> None:
    store = _store(tmp_path)

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text="/typo this should not become a topic idea",
            entities=[],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "command_ignored"
    assert store.list_capture_outbox() == ()


def test_process_message_queues_unfiltered_url_to_review_lane(tmp_path) -> None:
    store = _store(tmp_path)
    store.set_topic_mapping(456, "unfiltered")

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=456,
            text="https://example.com/unclear",
            entities=[FakeEntity(type="url", offset=0, length=len("https://example.com/unclear"))],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    rows = store.list_outbox()
    assert result.status == "queued"
    assert result.reply_text == "Queued 1 URL(s) for unfiltered review."
    assert rows[0].topic_slug == "unfiltered"


def test_process_message_captures_inspiration_text_without_url(tmp_path) -> None:
    store = _store(tmp_path)
    store.set_topic_mapping(789, "inspiration-capture")

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=789,
            text="Research whether public lidar can guide rockhounding trips.",
            entities=[],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    captures = store.list_capture_outbox()
    assert result.status == "captured"
    assert result.capture_ids == (1,)
    assert result.reply_text == "Captured inspiration note."
    assert captures[0].capture_type == "inspiration-capture"
    assert captures[0].text == "Research whether public lidar can guide rockhounding trips."
    assert store.list_outbox() == ()


def test_process_message_ignores_inspiration_map_hint(tmp_path) -> None:
    store = _store(tmp_path)
    store.set_topic_mapping(789, "inspiration-capture")

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=789,
            text="map inspiration-capture",
            entities=[],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "no_urls"
    assert store.list_capture_outbox() == ()


def test_process_message_deduplicates_normalized_urls(tmp_path) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="Read this",
        entities=[FakeEntity(type="text_link", url="https://www.example.com/a?utm_source=t")],
    )

    first = process_message(message, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))
    second = process_message(message, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))

    assert first.status == "queued"
    assert second.status == "duplicate"
    assert len(store.list_outbox()) == 1
    assert second.batch_id is None
    assert store.get_queue_batch(2) is None


def test_process_message_reuses_batch_for_redelivered_message(tmp_path) -> None:
    store = _store(tmp_path)
    first = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/a",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )
    second = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/b",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )

    first_result = process_message(first, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))
    second_result = process_message(second, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))

    assert first_result.batch_id == 1
    assert second_result.batch_id == 1


def test_process_message_rejects_private_resolution(tmp_path) -> None:
    store = _store(tmp_path)

    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text="https://example.com/a",
            entities=[FakeEntity(type="url", offset=0, length=21)],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("10.0.0.5"),
    )

    assert result.status == "rejected"
    assert store.list_outbox() == ()


def test_process_message_reports_mixed_queued_and_rejected_urls(tmp_path) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="Read this",
        entities=[
            FakeEntity(type="text_link", url="https://example.com/a"),
            FakeEntity(type="text_link", url="file:///etc/passwd"),
        ],
    )

    result = process_message(message, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))

    assert result.status == "queued"
    assert result.reply_text == "Queued 1 URL(s) for obsidian. Rejected 1 URL(s)."
    assert result.rejected_urls == ("file:///etc/passwd",)


def test_allowed_command_chat_accepts_configured_group_and_user_dm() -> None:
    group_message = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbdigest",
        entities=[],
    )
    dm_message = FakeMessage(
        chat=FakeChat(222, type="private"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbreply approve item 1",
        entities=[],
    )
    other_group_message = FakeMessage(
        chat=FakeChat(-100999),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbdigest",
        entities=[],
    )

    assert _is_allowed_command_chat(group_message, -100123)
    assert _is_allowed_command_chat(dm_message, -100123)
    assert not _is_allowed_command_chat(other_group_message, -100123)


def test_allowed_command_chat_rejects_group_with_user_id_chat_id() -> None:
    message = FakeMessage(
        chat=FakeChat(111, type="group"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbdigest",
        entities=[],
    )

    assert not _is_allowed_command_chat(message, -100123)


def test_url_in_private_dm_is_not_captured(tmp_path) -> None:
    store = _store(tmp_path)

    result = process_message(
        FakeMessage(
            chat=FakeChat(222, type="private"),
            from_user=FakeUser(111),
            message_thread_id=None,
            text="https://example.com/a",
            entities=[FakeEntity(type="url", offset=0, length=21)],
        ),
        store,
        allowed_chat_id=-100123,
        resolver=_resolver("93.184.216.34"),
    )

    assert result.status == "ignored_chat"
    assert store.list_outbox() == ()
    assert store.list_capture_outbox() == ()


def test_kbremind_from_private_dm_by_allowed_user_succeeds(tmp_path) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(222, type="private"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbremind tomorrow",
        entities=[],
    )
    context = SimpleNamespace(
        args=["tomorrow"],
        application=SimpleNamespace(bot_data={"state_store": store, "allowed_chat_id": -100123}),
    )

    asyncio.run(handle_kbremind(SimpleNamespace(effective_message=message), context))

    assert message.replies is not None
    assert message.replies[0].startswith("Okay, I will remind you about the latest weekly KB digest at ")


def test_kbdigest_from_private_dm_by_allowed_user_succeeds(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(222, type="private"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbdigest",
        entities=[],
    )
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={"state_store": store, "allowed_chat_id": -100123, "kb_root": str(tmp_path / "kb")}
        ),
    )

    def fake_weekly_digest(*_args, **_kwargs):
        return SimpleNamespace(telegram_text="digest result")

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(runtime, "ClaudeCliDigestAgent", lambda: object())
    monkeypatch.setattr(runtime, "weekly_digest", fake_weekly_digest)
    monkeypatch.setattr(runtime.asyncio, "to_thread", fake_to_thread)

    asyncio.run(handle_kbdigest(SimpleNamespace(effective_message=message), context))

    assert message.replies == ["Building weekly KB digest...", "digest result"]


def test_kbreply_from_private_dm_by_allowed_user_succeeds(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(222, type="private"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbreply approve item 1",
        entities=[],
    )
    context = SimpleNamespace(
        args=["approve", "item", "1"],
        application=SimpleNamespace(bot_data={"state_store": store, "allowed_chat_id": -100123}),
    )

    def fake_process_digest_reply(*_args, **_kwargs):
        return SimpleNamespace(telegram_text="reply result")

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(runtime, "ClaudeCliDigestAgent", lambda: object())
    monkeypatch.setattr(runtime, "process_digest_reply", fake_process_digest_reply)
    monkeypatch.setattr(runtime.asyncio, "to_thread", fake_to_thread)

    asyncio.run(handle_kbreply(SimpleNamespace(effective_message=message), context))

    assert message.replies == ["Sending your digest reply to Claude...", "reply result"]


def test_command_from_private_dm_by_unknown_user_is_rejected(tmp_path) -> None:
    message = FakeMessage(
        chat=FakeChat(222, type="private"),
        from_user=FakeUser(999),
        message_thread_id=None,
        text="/kbremind tomorrow",
        entities=[],
    )
    context = SimpleNamespace(
        args=["tomorrow"],
        application=SimpleNamespace(bot_data={"state_store": _store(tmp_path), "allowed_chat_id": -100123}),
    )

    asyncio.run(handle_kbremind(SimpleNamespace(effective_message=message), context))

    assert message.replies == ["This Telegram user is not allowed for kb-feed."]


def test_command_from_unrelated_group_is_ignored(tmp_path) -> None:
    message = FakeMessage(
        chat=FakeChat(-100999, type="supergroup"),
        from_user=FakeUser(111),
        message_thread_id=None,
        text="/kbremind tomorrow",
        entities=[],
    )
    context = SimpleNamespace(
        args=["tomorrow"],
        application=SimpleNamespace(bot_data={"state_store": _store(tmp_path), "allowed_chat_id": -100123}),
    )

    asyncio.run(handle_kbremind(SimpleNamespace(effective_message=message), context))

    assert message.replies is None


def test_undo_callback_cancels_batch(tmp_path) -> None:
    store = _store(tmp_path)
    message = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/a",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )
    queued = process_message(message, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))

    result = process_undo_callback(
        f"kbundo:{queued.batch_id}",
        store,
        chat_id=-100123,
        user_id=111,
        allowed_chat_id=-100123,
    )

    assert result.status == "undo_canceled"
    assert result.reply_text == "Undid 1 queued URL(s)."
    assert store.list_outbox()[0].status == "canceled"


def test_undo_callback_rejects_allowed_non_source_user(tmp_path) -> None:
    store = _store(tmp_path)
    store.allow_user(222)
    message = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/a",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )
    queued = process_message(message, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))

    result = process_undo_callback(
        f"kbundo:{queued.batch_id}",
        store,
        chat_id=-100123,
        user_id=222,
        allowed_chat_id=-100123,
    )

    assert result.status == "unauthorized_user"
    assert store.list_outbox()[0].status == "pending"


def test_undo_callback_rejects_malformed_data(tmp_path) -> None:
    result = process_undo_callback(
        "kbundo:not-an-id",
        _store(tmp_path),
        chat_id=-100123,
        user_id=111,
        allowed_chat_id=-100123,
    )

    assert result.status == "undo_bad_callback"


def test_undo_reply_to_source_message_cancels_batch(tmp_path) -> None:
    store = _store(tmp_path)
    source = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/a",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )
    process_message(source, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))
    command = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="/kbundo",
        entities=[],
        message_id=88,
        reply_to_message=source,
    )

    result = process_undo_reply(command, store, allowed_chat_id=-100123)

    assert result.status == "undo_canceled"
    assert store.list_outbox()[0].status == "canceled"


def test_undo_reply_to_confirmation_message_cancels_batch(tmp_path) -> None:
    store = _store(tmp_path)
    source = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="https://example.com/a",
        entities=[FakeEntity(type="url", offset=0, length=21)],
        message_id=77,
    )
    queued = process_message(source, store, allowed_chat_id=-100123, resolver=_resolver("93.184.216.34"))
    store.attach_batch_confirmation(queued.batch_id or 0, 99)
    confirmation = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(999),
        message_thread_id=123,
        text="Queued 1 URL(s) for obsidian.",
        entities=[],
        message_id=99,
    )
    command = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="/kbundo",
        entities=[],
        message_id=100,
        reply_to_message=confirmation,
    )

    result = process_undo_reply(command, store, allowed_chat_id=-100123)

    assert result.status == "undo_canceled"
    assert store.list_outbox()[0].status == "canceled"


def test_undo_reply_without_target_refuses(tmp_path) -> None:
    command = FakeMessage(
        chat=FakeChat(-100123),
        from_user=FakeUser(111),
        message_thread_id=123,
        text="/kbundo",
        entities=[],
    )

    result = process_undo_reply(command, _store(tmp_path), allowed_chat_id=-100123)

    assert result.status == "undo_missing_target"


def test_map_command_maps_new_thread(tmp_path) -> None:
    store = _store(tmp_path)
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=555,
            text="map obsidian",
        ),
        store,
        allowed_chat_id=-100123,
        kb_root=_kb_fixture(tmp_path),
    )

    assert result.status == "mapped"
    assert store.topic_slug_for_thread(555) == "obsidian"


def test_map_command_slash_form(tmp_path) -> None:
    store = _store(tmp_path)
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=556,
            text="/map obsidian",
        ),
        store,
        allowed_chat_id=-100123,
        kb_root=_kb_fixture(tmp_path),
    )

    assert result.status == "mapped"
    assert store.topic_slug_for_thread(556) == "obsidian"


def test_map_command_rejects_unknown_topic(tmp_path) -> None:
    store = _store(tmp_path)
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=557,
            text="map does-not-exist",
        ),
        store,
        allowed_chat_id=-100123,
        kb_root=_kb_fixture(tmp_path),
    )

    assert result.status == "map_no_topic"
    assert store.topic_slug_for_thread(557) is None


def test_map_command_requires_thread(tmp_path) -> None:
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=None,
            text="map obsidian",
        ),
        _store(tmp_path),
        allowed_chat_id=-100123,
        kb_root=_kb_fixture(tmp_path),
    )

    assert result.status == "map_no_thread"


def test_map_command_leaves_already_mapped_thread_untouched(tmp_path) -> None:
    store = _store(tmp_path)  # thread 123 -> obsidian
    kb_root = _kb_fixture(tmp_path)
    (kb_root / "topics" / "prediction-markets").mkdir(parents=True)
    result = process_message(
        FakeMessage(
            chat=FakeChat(-100123),
            from_user=FakeUser(111),
            message_thread_id=123,
            text="map prediction-markets",
        ),
        store,
        allowed_chat_id=-100123,
        kb_root=kb_root,
    )

    # Already-mapped threads are not re-mapped via chat; the hint is ignored.
    assert result.status != "mapped"
    assert store.topic_slug_for_thread(123) == "obsidian"
