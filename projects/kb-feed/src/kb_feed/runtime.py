from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from kb_feed.errors import KbFeedError, UrlRejected
from kb_feed.digest import (
    default_digest_agent,
    process_digest_reply,
    render_digest_section,
    snooze_weekly_digest,
    split_telegram_message,
    weekly_digest,
)
from kb_feed.doctor import build_doctor_report
from kb_feed.locking import exclusive_kb_write_lock
from kb_feed.nudge import build_nudge_text, count_pending_proposals
from kb_feed.security import Resolver
from kb_feed.state import CancelBatchResult, StateStore, validate_topic_slug
from kb_feed.topic_init import create_topic_skeleton
from kb_feed.urls import MessageEntityLike, extract_urls_from_entities, normalize_url
from kb_feed.writer import write_outbox_row


LOGGER = logging.getLogger(__name__)


class UserLike(Protocol):
    id: int


class ChatLike(Protocol):
    id: int
    type: str


class MessageLike(Protocol):
    message_id: int
    chat: ChatLike
    from_user: UserLike | None
    message_thread_id: int | None
    reply_to_message: MessageLike | None
    text: str | None
    caption: str | None
    entities: tuple[MessageEntityLike, ...] | list[MessageEntityLike] | None
    caption_entities: tuple[MessageEntityLike, ...] | list[MessageEntityLike] | None

    def parse_entity(self, entity: MessageEntityLike) -> str:
        ...


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    reply_text: str | None = None
    outbox_ids: tuple[int, ...] = ()
    duplicate_urls: tuple[str, ...] = ()
    rejected_urls: tuple[str, ...] = ()
    capture_ids: tuple[int, ...] = ()
    auto_approved_ids: tuple[int, ...] = ()
    batch_id: int | None = None


def process_message(
    message: MessageLike,
    store: StateStore,
    *,
    allowed_chat_id: int,
    resolver: Resolver = socket.getaddrinfo,
    kb_root: Path | None = None,
    auto_approve_lanes: tuple[str, ...] = (),
    lock_path: Path | None = None,
    today: date | None = None,
) -> RuntimeResult:
    if int(message.chat.id) != allowed_chat_id:
        return RuntimeResult(status="ignored_chat")

    user_id = _user_id(message)
    if user_id is None or not store.is_user_allowed(user_id):
        return RuntimeResult(status="unauthorized_user", reply_text="This Telegram user is not allowed for kb-feed.")

    store.touch_activity()

    thread_id = int(message.message_thread_id or 0)

    map_slug = _map_command(_message_text(message))
    if map_slug is not None and (thread_id <= 0 or store.topic_slug_for_thread(thread_id) is None):
        return _apply_topic_map(store, thread_id, map_slug, kb_root)

    if thread_id <= 0:
        raw_urls = _extract_message_urls(message)
        if not raw_urls:
            return RuntimeResult(status="no_urls")
        # Persist the drop so silently-lost URLs are visible to `kb-feed doctor`.
        store.increment_meta("dropped_missing_thread_total")
        return RuntimeResult(status="missing_thread", reply_text="This message is not in a mapped topic thread.")

    topic_slug = store.topic_slug_for_thread(thread_id)
    quarantine_thread: int | None = None
    if topic_slug is None:
        raw_urls = _extract_message_urls(message)
        if not raw_urls:
            return RuntimeResult(status="no_urls")
        # Quarantine (lossless): an unmapped TOPIC thread is "a topic I made but have not
        # mapped yet" — route its URLs to the global inbox (Links.md) instead of dropping
        # them. General chat (thread<=0) is deliberately not handled this way.
        store.increment_meta("quarantined_unmapped_thread_total")
        store.increment_meta(f"quarantined_unmapped_thread:{thread_id}")
        quarantine_thread = thread_id
        topic_slug = "unfiltered"

    if _is_command_like(_message_text(message)):
        return RuntimeResult(status="command_ignored")

    raw_urls = _extract_message_urls(message)
    if topic_slug == "inspiration-capture":
        text = _message_text(message).strip()
        if not text or _is_map_hint(text):
            return RuntimeResult(status="no_urls")
        batch_id = _create_batch_for_message(message, store, thread_id=thread_id, user_id=user_id)
        capture_id = store.enqueue_capture(
            thread_id=thread_id,
            capture_type="inspiration-capture",
            text=text,
            batch_id=batch_id,
        )
        return RuntimeResult(
            status="captured",
            reply_text="Captured inspiration note.",
            capture_ids=(capture_id,),
            batch_id=batch_id,
        )

    if not raw_urls:
        if topic_slug != "unfiltered":
            text = _message_text(message).strip()
            if text and not _is_map_hint(text):
                batch_id = _create_batch_for_message(message, store, thread_id=thread_id, user_id=user_id)
                capture_id = store.enqueue_capture(
                    thread_id=thread_id,
                    capture_type="topic-idea",
                    topic_slug=topic_slug,
                    text=text,
                    batch_id=batch_id,
                )
                return RuntimeResult(
                    status="captured",
                    reply_text=f"Captured topic idea for {topic_slug}.",
                    capture_ids=(capture_id,),
                    batch_id=batch_id,
                )
        return RuntimeResult(status="no_urls")

    note = _message_note(message)
    if quarantine_thread is not None:
        tag = f"unmapped thread {quarantine_thread}"
        note = f"{tag} — {note}" if note else tag
    auto_approve = _should_auto_approve(
        message,
        topic_slug=topic_slug,
        auto_approve_lanes=auto_approve_lanes,
        kb_root=kb_root,
    )
    captured_on = today or date.today()
    outbox_ids: list[int] = []
    auto_approved_ids: list[int] = []
    duplicates: list[str] = []
    rejected: list[str] = []
    batch_id: int | None = None
    for raw_url in raw_urls:
        try:
            record = normalize_url(raw_url, resolver=resolver)
        except UrlRejected as exc:
            LOGGER.info("Rejected Telegram URL %s: %s", raw_url, exc)
            rejected.append(raw_url)
            continue

        if store.has_seen_url(record.normalized_url):
            duplicates.append(record.fetch_url)
            continue
        if batch_id is None:
            batch_id = _create_batch_for_message(message, store, thread_id=thread_id, user_id=user_id)
        result = store.enqueue_unseen_url(
            thread_id=thread_id,
            topic_slug=topic_slug,
            original_url=record.original_url,
            normalized_url=record.normalized_url,
            fetch_url=record.fetch_url,
            note=note,
            batch_id=batch_id,
            target_section="auto-approved" if auto_approve else "pending",
        )
        if result.is_duplicate:
            duplicates.append(record.fetch_url)
        elif result.outbox_id is not None:
            if auto_approve:
                row = store.get_outbox(result.outbox_id)
                if row is None:
                    raise KbFeedError(f"Queued auto-approved outbox row disappeared: {result.outbox_id}")
                with exclusive_kb_write_lock(kb_root or Path("~/Vault/knowledgebase").expanduser(), lock_path):
                    write_outbox_row(row, kb_root or Path("~/Vault/knowledgebase").expanduser(), captured_on)
                store.mark_outbox_written(row.id)
                auto_approved_ids.append(result.outbox_id)
            else:
                outbox_ids.append(result.outbox_id)

    if auto_approved_ids:
        reply = f"Auto-approved {len(auto_approved_ids)} URL(s) for {topic_slug}."
        if rejected:
            reply += f" Rejected {len(rejected)} URL(s)."
        return RuntimeResult(
            status="auto_approved",
            reply_text=reply,
            auto_approved_ids=tuple(auto_approved_ids),
            duplicate_urls=tuple(duplicates),
            rejected_urls=tuple(rejected),
            batch_id=batch_id,
        )
    if outbox_ids:
        if quarantine_thread is not None:
            reply = (
                f"No KB topic is mapped to this thread — saved {len(outbox_ids)} URL(s) to the global "
                f"inbox so nothing is lost. Send `map <slug>` here to route this thread's drops directly "
                f"(it will create the topic if it doesn't exist yet)."
            )
        else:
            target = "unfiltered review" if topic_slug == "unfiltered" else topic_slug
            reply = f"Queued {len(outbox_ids)} URL(s) for {target}."
        if rejected:
            reply += f" Rejected {len(rejected)} URL(s)."
        return RuntimeResult(
            status="quarantined" if quarantine_thread is not None else "queued",
            reply_text=reply,
            outbox_ids=tuple(outbox_ids),
            duplicate_urls=tuple(duplicates),
            rejected_urls=tuple(rejected),
            batch_id=batch_id,
        )
    if duplicates and not rejected:
        return RuntimeResult(
            status="duplicate",
            reply_text="Already queued.",
            duplicate_urls=tuple(duplicates),
        )
    if rejected and not duplicates:
        return RuntimeResult(
            status="rejected",
            reply_text="No supported public http(s) URLs found.",
            rejected_urls=tuple(rejected),
        )
    return RuntimeResult(
        status="no_new_urls",
        reply_text="No new supported URLs found.",
        duplicate_urls=tuple(duplicates),
        rejected_urls=tuple(rejected),
    )


def process_undo_callback(
    callback_data: str,
    store: StateStore,
    *,
    chat_id: int,
    user_id: int | None,
    allowed_chat_id: int,
) -> RuntimeResult:
    if chat_id != allowed_chat_id:
        return RuntimeResult(status="ignored_chat")
    if user_id is None or not store.is_user_allowed(user_id):
        return RuntimeResult(status="unauthorized_user", reply_text="This Telegram user is not allowed for kb-feed.")

    prefix, separator, raw_batch_id = callback_data.partition(":")
    if prefix != "kbundo" or not separator or not raw_batch_id.isdigit():
        return RuntimeResult(status="undo_bad_callback", reply_text="Invalid queue undo request.")

    batch = store.get_queue_batch(int(raw_batch_id))
    if batch is None or batch.chat_id != chat_id:
        return RuntimeResult(status="undo_not_found", reply_text="Queue batch not found.")
    result = store.cancel_queue_batch(batch.id, actor_user_id=user_id)
    return _undo_runtime_result(result)


def process_undo_reply(
    message: MessageLike,
    store: StateStore,
    *,
    allowed_chat_id: int,
) -> RuntimeResult:
    if int(message.chat.id) != allowed_chat_id:
        return RuntimeResult(status="ignored_chat")

    user_id = _user_id(message)
    if user_id is None or not store.is_user_allowed(user_id):
        return RuntimeResult(status="unauthorized_user", reply_text="This Telegram user is not allowed for kb-feed.")

    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is None:
        return RuntimeResult(
            status="undo_missing_target",
            reply_text="Reply to the original captured message or the bot's queue confirmation.",
        )

    chat_id = int(message.chat.id)
    thread_id = _thread_id(message) or _thread_id(reply_to)
    if thread_id <= 0:
        return RuntimeResult(
            status="undo_missing_target",
            reply_text="Reply to the original captured message or the bot's queue confirmation.",
        )

    target_message_id = _message_id(reply_to)
    if target_message_id <= 0:
        return RuntimeResult(status="undo_not_found", reply_text="Queue batch not found.")

    batch = store.find_batch_by_source_message(
        chat_id=chat_id,
        thread_id=thread_id,
        source_message_id=target_message_id,
    )
    if batch is None:
        batch = store.find_batch_by_confirmation_message(
            chat_id=chat_id,
            thread_id=thread_id,
            confirmation_message_id=target_message_id,
        )
    if batch is None:
        return RuntimeResult(status="undo_not_found", reply_text="Queue batch not found.")
    result = store.cancel_queue_batch(batch.id, actor_user_id=user_id)
    return _undo_runtime_result(result)


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.application.bot_data["state_store"]
    allowed_chat_id = int(context.application.bot_data["allowed_chat_id"])
    message = update.effective_message
    if message is None:
        return

    try:
        result = await asyncio.to_thread(
            process_message,
            message,
            store,
            allowed_chat_id=allowed_chat_id,
            kb_root=Path(context.application.bot_data["kb_root"]),
            auto_approve_lanes=tuple(context.application.bot_data.get("auto_approve_lanes", ())),
        )
    except KbFeedError as exc:
        LOGGER.exception("kb-feed message processing failed")
        await message.reply_text(f"kb-feed error: {exc}")
        return

    if result.reply_text:
        if result.batch_id is not None and (result.outbox_ids or result.capture_ids):
            confirmation = await message.reply_text(
                result.reply_text,
                reply_markup=_undo_markup(result.batch_id),
            )
            await asyncio.to_thread(
                store.attach_batch_confirmation,
                result.batch_id,
                int(confirmation.message_id),
            )
        else:
            await message.reply_text(result.reply_text)


async def handle_kbundo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    allowed_chat_id = int(context.application.bot_data["allowed_chat_id"])
    result = await asyncio.to_thread(process_undo_reply, message, store, allowed_chat_id=allowed_chat_id)
    if result.reply_text:
        await message.reply_text(result.reply_text)


async def handle_undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    store = context.application.bot_data["state_store"]
    allowed_chat_id = int(context.application.bot_data["allowed_chat_id"])
    message = query.message
    chat_id = int(message.chat.id) if message is not None else 0
    user_id = int(query.from_user.id) if query.from_user is not None else None
    result = await asyncio.to_thread(
        process_undo_callback,
        str(query.data or ""),
        store,
        chat_id=chat_id,
        user_id=user_id,
        allowed_chat_id=allowed_chat_id,
    )
    await query.answer(result.reply_text or "Queue undo handled.")
    if result.reply_text and message is not None:
        if result.status in {"undo_canceled", "undo_already_canceled"}:
            try:
                await query.edit_message_text(result.reply_text)
                return
            except Exception:
                LOGGER.exception("Failed to edit kb-feed undo confirmation")
        await message.reply_text(result.reply_text)


async def handle_start(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text("kb-feed is running. Send /kbhelp for commands.")


_KBHELP_TEXT = (
    "kb-feed commands:\n"
    "/kbhelp - this list\n"
    "/kbdoctor - health report: thread->topic mappings, blind topics, dropped-message "
    "counters, queue backlog, unapplied proposals\n"
    "/kbnudge - quick count of links/notes/proposals awaiting triage (no LLM)\n"
    "/kbdigest - build the weekly KB review digest: summary + section index (proposal-only)\n"
    "/kbdigest <section> - fetch one section: moves | topics | priorities | deferred | replies\n"
    "/kbreply <text> - reply to the latest digest (approve/rename/defer/skip/ingest)\n"
    "/kbremind [later|3h|1d|tomorrow] - snooze a digest reminder\n"
    "/kbundo - undo the most recent queued capture (reply to the bot's confirmation)\n"
    "\nIn a topic thread: drop a URL to queue it; plain text becomes a topic idea.\n"
    "Send `map <slug>` to map this thread to a KB topic — it CREATES the topic if it "
    "doesn't exist yet. URLs dropped in an unmapped thread are quarantined to the global "
    "inbox (never lost) until you map it."
)


async def handle_kbhelp(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text(_KBHELP_TEXT)


async def handle_kbdigest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    if not _is_allowed_command_user(message, store):
        await message.reply_text("This Telegram user is not allowed for kb-feed.")
        return
    if not _is_allowed_command_chat(message, int(context.application.bot_data["allowed_chat_id"])):
        LOGGER.info("Ignoring digest command from non-configured chat %s", message.chat.id)
        return

    # `/kbdigest <section>` fetches a section of the LAST digest (no LLM call); bare
    # `/kbdigest` builds a fresh digest and replies with the summary + section index.
    section_arg = " ".join(context.args or ()).strip()
    if section_arg:
        text = await asyncio.to_thread(render_digest_section, store, section_arg)
        await _reply_long(message, text)
        return

    kb_root = Path(context.application.bot_data["kb_root"])
    proposals_dir = store.path.parent / "proposals"
    await message.reply_text("Building weekly KB digest...")
    try:
        report = await asyncio.to_thread(
            weekly_digest,
            store,
            kb_root,
            agent=default_digest_agent(),
            proposals_dir=proposals_dir,
        )
    except KbFeedError as exc:
        LOGGER.exception("kb-feed digest failed")
        await message.reply_text(f"kb-feed digest error: {exc}")
        return
    await _reply_long(message, report.telegram_text)


async def handle_kbnudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    if not _is_allowed_command_user(message, store):
        await message.reply_text("This Telegram user is not allowed for kb-feed.")
        return
    if not _is_allowed_command_chat(message, int(context.application.bot_data["allowed_chat_id"])):
        return
    kb_root = Path(context.application.bot_data["kb_root"])
    proposals = count_pending_proposals(store.path.parent / "proposals")
    text = await asyncio.to_thread(build_nudge_text, kb_root, proposals_pending=proposals)
    await message.reply_text(text or "KB inbox is clear — nothing awaiting triage.")


async def handle_kbdoctor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    if not _is_allowed_command_user(message, store):
        await message.reply_text("This Telegram user is not allowed for kb-feed.")
        return
    if not _is_allowed_command_chat(message, int(context.application.bot_data["allowed_chat_id"])):
        LOGGER.info("Ignoring doctor command from non-configured chat %s", message.chat.id)
        return
    kb_root = Path(context.application.bot_data["kb_root"])
    report = await asyncio.to_thread(build_doctor_report, store, kb_root)
    await _reply_long(message, report)


async def handle_kbremind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    if not _is_allowed_command_user(message, store):
        await message.reply_text("This Telegram user is not allowed for kb-feed.")
        return
    if not _is_allowed_command_chat(message, int(context.application.bot_data["allowed_chat_id"])):
        LOGGER.info("Ignoring reminder command from non-configured chat %s", message.chat.id)
        return
    value = " ".join(context.args or ()) or "later"
    try:
        until = snooze_weekly_digest(store, value)
    except KbFeedError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(f"Okay, I will remind you about the latest weekly KB digest at {until.isoformat()}.")


async def handle_kbreply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    store = context.application.bot_data["state_store"]
    if not _is_allowed_command_user(message, store):
        await message.reply_text("This Telegram user is not allowed for kb-feed.")
        return
    if not _is_allowed_command_chat(message, int(context.application.bot_data["allowed_chat_id"])):
        LOGGER.info("Ignoring digest reply command from non-configured chat %s", message.chat.id)
        return
    decision_text = _digest_decision_text(message.text or "", context.args or [])
    if not decision_text:
        await message.reply_text("Reply with what to approve, rename, defer, skip, or mark ingest-now.")
        return
    proposals_dir = store.path.parent / "proposals"
    await message.reply_text("Processing your digest reply...")
    try:
        report = await asyncio.to_thread(
            process_digest_reply,
            store,
            decision_text=decision_text,
            agent=default_digest_agent(),
            proposals_dir=proposals_dir,
        )
    except KbFeedError as exc:
        LOGGER.exception("kb-feed digest reply failed")
        await message.reply_text(f"kb-feed digest reply error: {exc}")
        return
    await _reply_long(message, report.telegram_text)


def build_application(
    *,
    bot_token: str,
    store: StateStore,
    allowed_chat_id: int,
    kb_root: Path | None = None,
    auto_approve_lanes: tuple[str, ...] = (),
) -> Application:
    app = Application.builder().token(bot_token).build()
    app.bot_data["state_store"] = store
    app.bot_data["allowed_chat_id"] = allowed_chat_id
    app.bot_data["kb_root"] = str(kb_root or Path("~/Vault/knowledgebase").expanduser())
    app.bot_data["auto_approve_lanes"] = tuple(auto_approve_lanes)
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("kbhelp", handle_kbhelp))
    app.add_handler(CommandHandler("help", handle_kbhelp))
    app.add_handler(CommandHandler("kbdoctor", handle_kbdoctor))
    app.add_handler(CommandHandler("kbnudge", handle_kbnudge))
    app.add_handler(CommandHandler("kbdigest", handle_kbdigest))
    app.add_handler(CommandHandler("kbremind", handle_kbremind))
    app.add_handler(CommandHandler("kbreply", handle_kbreply))
    app.add_handler(CommandHandler("kbapprove", handle_kbreply))
    app.add_handler(CommandHandler("kbdefer", handle_kbreply))
    app.add_handler(CommandHandler("kbskip", handle_kbreply))
    app.add_handler(CommandHandler("kbrename", handle_kbreply))
    app.add_handler(CommandHandler("kbingest", handle_kbreply))
    app.add_handler(CommandHandler("kbundo", handle_kbundo))
    app.add_handler(CallbackQueryHandler(handle_undo_callback, pattern=r"^kbundo:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT | filters.CaptionRegex(r".+"), handle_update))
    return app


def run_polling(
    *,
    bot_token: str,
    store: StateStore,
    allowed_chat_id: int,
    kb_root: Path | None = None,
    auto_approve_lanes: tuple[str, ...] = (),
) -> None:
    app = build_application(
        bot_token=bot_token,
        store=store,
        allowed_chat_id=allowed_chat_id,
        kb_root=kb_root,
        auto_approve_lanes=auto_approve_lanes,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _extract_message_urls(message: MessageLike) -> list[str]:
    text = _message_text(message)
    entities = message.entities or message.caption_entities or ()
    return extract_urls_from_entities(text, entities, parse_entity=message.parse_entity)


def _should_auto_approve(
    message: MessageLike,
    *,
    topic_slug: str,
    auto_approve_lanes: tuple[str, ...],
    kb_root: Path | None,
) -> bool:
    if kb_root is None:
        return False
    if topic_slug in {"unfiltered", "inspiration-capture"}:
        return False
    if topic_slug not in set(auto_approve_lanes):
        return False
    return _message_starts_with_url(message)


def _message_starts_with_url(message: MessageLike) -> bool:
    text = _message_text(message)
    entities = message.entities or message.caption_entities or ()
    url_offsets = [
        int(entity.offset)
        for entity in entities
        if getattr(entity, "type", "") == "url"
    ]
    if not text or not url_offsets:
        return False
    return min(url_offsets) == len(text) - len(text.lstrip())


def _message_note(message: MessageLike) -> str | None:
    text = _message_text(message)
    if not text:
        return None
    entities = message.entities or message.caption_entities or ()
    spans = sorted(
        (int(entity.offset), int(entity.offset) + int(entity.length))
        for entity in entities
        if getattr(entity, "type", "") == "url"
    )
    if not spans:
        cleaned = " ".join(text.split())
        return cleaned or None

    chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            chunks.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        chunks.append(text[cursor:])
    cleaned = " ".join("".join(chunks).split())
    return cleaned or None


def _message_text(message: MessageLike) -> str:
    return str(message.text or message.caption or "")


def _message_id(message: MessageLike) -> int:
    return int(getattr(message, "message_id", 0) or 0)


def _thread_id(message: MessageLike) -> int:
    return int(getattr(message, "message_thread_id", None) or 0)


def _create_batch_for_message(message: MessageLike, store: StateStore, *, thread_id: int, user_id: int) -> int | None:
    source_message_id = _message_id(message)
    if source_message_id <= 0:
        return None
    return store.create_queue_batch(
        chat_id=int(message.chat.id),
        thread_id=thread_id,
        source_message_id=source_message_id,
        source_user_id=user_id,
    )


def _undo_markup(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"kbundo:{batch_id}")]])


def _undo_runtime_result(result: CancelBatchResult) -> RuntimeResult:
    if result.not_found:
        return RuntimeResult(status="undo_not_found", reply_text="Queue batch not found.")
    if result.unauthorized:
        return RuntimeResult(status="unauthorized_user", reply_text="This Telegram user cannot undo that queue batch.")
    if result.canceled:
        return RuntimeResult(status="undo_canceled", reply_text=_undo_success_text(result))
    if result.already_canceled:
        return RuntimeResult(status="undo_already_canceled", reply_text="That queue batch was already undone.")
    if result.writing:
        return RuntimeResult(status="undo_writing", reply_text="That queue batch is being written; try again after flush finishes.")
    if result.written:
        return RuntimeResult(
            status="undo_written",
            reply_text="That item was already written to the KB; queue undo cannot remove it.",
        )
    if result.failed:
        return RuntimeResult(status="undo_failed_row", reply_text="That queue batch has failed rows; use outbox recovery tools.")
    return RuntimeResult(status="undo_noop", reply_text="No pending queued items found for that batch.")


def _undo_success_text(result: CancelBatchResult) -> str:
    parts: list[str] = []
    if result.url_canceled:
        parts.append(f"Undid {result.url_canceled} queued URL(s)")
    if result.capture_canceled:
        noun = "note" if result.capture_canceled == 1 else "notes"
        parts.append(f"Undid {result.capture_canceled} queued {noun}")
    text = " and ".join(parts) + "."
    leftovers: list[str] = []
    if result.written:
        leftovers.append(f"{result.written} item(s) already written to the KB")
    if result.writing:
        leftovers.append(f"{result.writing} item(s) currently being written")
    if result.failed:
        leftovers.append(f"{result.failed} failed item(s) left unchanged")
    if leftovers:
        text += " " + "; ".join(leftovers) + "."
    return text


def _is_map_hint(text: str) -> bool:
    parts = text.strip().split()
    return len(parts) == 2 and parts[0].lower() == "map"


def _map_command(text: str) -> str | None:
    """Return the target topic slug if the message is a `map <slug>` or `/map <slug>` command."""
    parts = text.strip().split()
    if len(parts) == 2 and parts[0].lower().lstrip("/") == "map":
        return parts[1]
    return None


def _apply_topic_map(
    store: StateStore,
    thread_id: int,
    slug: str,
    kb_root: Path | None,
) -> RuntimeResult:
    """Map the current Telegram thread to a KB topic in response to an in-chat map command."""
    if thread_id <= 0:
        return RuntimeResult(
            status="map_no_thread",
            reply_text="Send the map command inside the topic thread you want to map, not the General chat.",
        )
    try:
        slug = validate_topic_slug(slug)
    except KbFeedError:
        return RuntimeResult(
            status="map_bad_slug",
            reply_text=f"'{slug}' is not a valid KB topic slug (use lowercase, hyphen-separated).",
        )
    created = False
    if kb_root is not None and not (kb_root / "topics" / slug).exists():
        with exclusive_kb_write_lock(kb_root):
            created = create_topic_skeleton(kb_root, slug)
    existing = store.topic_slug_for_thread(thread_id)
    store.set_topic_mapping(thread_id, slug)
    if created:
        reply = f"Created KB topic '{slug}' and mapped this thread to it. Links dropped here will queue under {slug}."
    elif existing == slug:
        reply = f"This thread is already mapped to KB topic '{slug}'."
    elif existing:
        reply = f"Re-mapped this thread from '{existing}' to KB topic '{slug}'."
    else:
        reply = f"Mapped this thread to KB topic '{slug}'. Links dropped here will queue under {slug}."
    return RuntimeResult(status="mapped", reply_text=reply)


def _is_command_like(text: str) -> bool:
    return text.strip().startswith("/")


def _user_id(message: MessageLike) -> int | None:
    user = message.from_user
    if user is None:
        return None
    return int(user.id)


def _is_allowed_command_user(message: MessageLike, store: StateStore) -> bool:
    user_id = _user_id(message)
    return user_id is not None and store.is_user_allowed(user_id)


def _is_allowed_command_chat(message: MessageLike, allowed_chat_id: int) -> bool:
    chat_id = int(message.chat.id)
    chat_type = str(getattr(message.chat, "type", ""))
    return chat_id == allowed_chat_id or chat_type == "private"


def _digest_decision_text(raw_text: str, args: list[str] | tuple[str, ...]) -> str:
    command = (raw_text.strip().split(maxsplit=1)[0] if raw_text.strip() else "").split("@", 1)[0].lower().lstrip("/")
    body = " ".join(str(arg) for arg in args).strip()
    if command == "kbreply":
        return body
    prefixes = {
        "kbapprove": "approve",
        "kbdefer": "defer",
        "kbskip": "skip",
        "kbrename": "rename",
        "kbingest": "mark ingest-now",
    }
    prefix = prefixes.get(command, "reply")
    return f"{prefix} {body}".strip()


async def _reply_long(message, text: str) -> None:
    for chunk in split_telegram_message(text.strip() or "(empty digest)"):
        await message.reply_text(chunk)
