from __future__ import annotations

import logging
import argparse
from pathlib import Path

from kb_feed.bootstrap import bootstrap_seen_urls
from kb_feed.config import load_config
from kb_feed.digest import ClaudeCliDigestAgent, process_digest_reply, remind_due_digest, send_digest_to_users, weekly_digest
from kb_feed.dry_run import LIVE_KB_ROOT, dry_run_update, parse_topic_map
from kb_feed.llm import LlmUnavailable, load_llm_config_from_env, LlmClient
from kb_feed.review import assign_link, promote_inspiration
from kb_feed.runtime import run_polling
from kb_feed.state import DEFAULT_STATE_DB, StateStore
from kb_feed.triage import (
    DEFAULT_INACTIVITY_HOURS,
    triage_inspiration,
    triage_links,
)
from kb_feed.writer import flush_outbox


LOGGER = logging.getLogger(__name__)


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(level=getattr(logging, level_name.upper(), logging.INFO))
    for logger_name in ("httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kb-feed")
    subcommands = parser.add_subparsers(dest="command")

    dry_run = subcommands.add_parser("dry-run", help="Apply one Telegram update JSON to a non-live KB fixture.")
    dry_run.add_argument("update_json", type=Path)
    dry_run.add_argument("--kb-root", type=Path, required=True)
    dry_run.add_argument("--state-db", type=Path, default=None, help="Load topic mappings from this SQLite DB.")
    dry_run.add_argument("--map", action="append", default=[], metavar="THREAD_ID=topic-slug")

    poll = subcommands.add_parser("poll", help="Run the Telegram long-polling intake loop.")
    poll.add_argument("--config", type=Path, default=None)
    poll.add_argument("--state-db", type=Path, default=None)

    state = subcommands.add_parser("state", help="Manage the local SQLite state database.")
    state_subcommands = state.add_subparsers(dest="state_command", required=True)
    state_init = state_subcommands.add_parser("init", help="Create or migrate the state database.")
    _add_state_db_arg(state_init)
    state_bootstrap_seen = state_subcommands.add_parser(
        "bootstrap-seen",
        help="Seed seen_urls from existing KB reading lists and dive queue.",
    )
    _add_state_db_arg(state_bootstrap_seen)
    state_bootstrap_seen.add_argument("--kb-root", type=Path, required=True)

    topic_map = subcommands.add_parser("topic-map", help="Manage Telegram thread to KB topic mappings.")
    topic_map_subcommands = topic_map.add_subparsers(dest="topic_map_command", required=True)
    topic_map_set = topic_map_subcommands.add_parser("set", help="Set one thread mapping.")
    _add_state_db_arg(topic_map_set)
    topic_map_set.add_argument("thread_id", type=int)
    topic_map_set.add_argument("topic_slug")
    topic_map_list = topic_map_subcommands.add_parser("list", help="List thread mappings.")
    _add_state_db_arg(topic_map_list)
    topic_map_remove = topic_map_subcommands.add_parser("remove", help="Remove one thread mapping.")
    _add_state_db_arg(topic_map_remove)
    topic_map_remove.add_argument("thread_id", type=int)

    user = subcommands.add_parser("user", help="Manage allowed Telegram users.")
    user_subcommands = user.add_subparsers(dest="user_command", required=True)
    user_allow = user_subcommands.add_parser("allow", help="Allow one Telegram user ID.")
    _add_state_db_arg(user_allow)
    user_allow.add_argument("user_id", type=int)
    user_revoke = user_subcommands.add_parser("revoke", help="Revoke one Telegram user ID.")
    _add_state_db_arg(user_revoke)
    user_revoke.add_argument("user_id", type=int)
    user_list = user_subcommands.add_parser("list", help="List allowed Telegram user IDs.")
    _add_state_db_arg(user_list)

    outbox = subcommands.add_parser("outbox", help="Inspect queued KB writes.")
    outbox_subcommands = outbox.add_subparsers(dest="outbox_command", required=True)
    outbox_list = outbox_subcommands.add_parser("list", help="List outbox rows.")
    _add_state_db_arg(outbox_list)
    outbox_list.add_argument("--status", default=None)
    outbox_retry = outbox_subcommands.add_parser("retry", help="Move failed outbox rows back to pending.")
    _add_state_db_arg(outbox_retry)
    outbox_retry.add_argument("--id", type=int, action="append", default=[], dest="row_ids")
    outbox_flush = outbox_subcommands.add_parser("flush", help="Write pending outbox rows to KB markdown.")
    _add_state_db_arg(outbox_flush)
    outbox_flush.add_argument("--kb-root", type=Path, required=True)
    outbox_flush.add_argument("--limit", type=int, default=None)
    outbox_flush.add_argument("--allow-live-kb", action="store_true")
    outbox_flush.add_argument("--lock-path", type=Path, default=None)
    outbox_flush.add_argument("--commit", action="store_true", help="Commit written KB queue files when debounce rules allow.")
    outbox_flush.add_argument("--force-commit", action="store_true", help="Commit written KB queue files immediately.")
    outbox_flush.add_argument("--commit-debounce-seconds", type=int, default=300)
    outbox_flush.add_argument("--commit-max-pending", type=int, default=20)
    outbox_flush.add_argument("--git-user-name", default="kb-bot")
    outbox_flush.add_argument("--git-user-email", default="noreply@kb-feed.local")

    triage = subcommands.add_parser("triage", help="Run LLM triage over capture lanes.")
    triage_subcommands = triage.add_subparsers(dest="triage_command", required=True)
    triage_links_p = triage_subcommands.add_parser(
        "links",
        help="Propose KB topic assignments for unchecked items in readinglist/Links.md.",
    )
    _add_state_db_arg(triage_links_p)
    triage_links_p.add_argument("--kb-root", type=Path, required=True)
    triage_links_p.add_argument(
        "--inactivity-hours",
        type=float,
        default=DEFAULT_INACTIVITY_HOURS,
        help="Refuse to run until the bot has been inactive at least this many hours (default 2).",
    )
    triage_links_p.add_argument("--limit", type=int, default=None)
    triage_links_p.add_argument(
        "--force",
        action="store_true",
        help="Bypass the inactivity gate. Triage still only writes proposals, never the live KB.",
    )
    triage_links_p.add_argument("--proposals-dir", type=Path, default=None)

    triage_inspiration_p = triage_subcommands.add_parser(
        "inspiration",
        help="Propose research items for unchecked notes in readinglist/topics.md.",
    )
    _add_state_db_arg(triage_inspiration_p)
    triage_inspiration_p.add_argument("--kb-root", type=Path, required=True)
    triage_inspiration_p.add_argument("--limit", type=int, default=None)
    triage_inspiration_p.add_argument("--proposals-dir", type=Path, default=None)

    review = subcommands.add_parser("review", help="Apply reviewed capture-lane proposals to KB queues.")
    review_subcommands = review.add_subparsers(dest="review_command", required=True)
    review_assign = review_subcommands.add_parser(
        "assign-link",
        help="Move one unchecked global URL inbox item into a topic reading list.",
    )
    review_assign.add_argument("--kb-root", type=Path, required=True)
    review_assign.add_argument("--url", required=True)
    review_assign.add_argument("--topic", required=True)
    review_assign.add_argument("--reason", default=None)
    review_assign.add_argument(
        "--dive",
        action="store_true",
        help="Also add the item to _dive-queue.md when it is an intentional investigation target.",
    )
    review_assign.add_argument("--allow-live-kb", action="store_true")
    review_assign.add_argument("--lock-path", type=Path, default=None)

    review_promote = review_subcommands.add_parser(
        "promote-inspiration",
        help="Promote one unchecked inspiration note into a topic dive queue.",
    )
    review_promote.add_argument("--kb-root", type=Path, required=True)
    review_promote.add_argument("--contains", required=True, help="Unique substring matching the source inbox note.")
    review_promote.add_argument("--topic", required=True)
    review_promote.add_argument("--idea", default=None, help="Optional rewritten dive-queue item.")
    review_promote.add_argument("--allow-live-kb", action="store_true")
    review_promote.add_argument("--lock-path", type=Path, default=None)

    digest = subcommands.add_parser("digest", help="Run or deliver weekly KB review digests.")
    digest_subcommands = digest.add_subparsers(dest="digest_command", required=True)
    digest_weekly = digest_subcommands.add_parser(
        "weekly",
        help="Generate a weekly proposal digest for KB queue review.",
    )
    _add_state_db_arg(digest_weekly)
    digest_weekly.add_argument("--kb-root", type=Path, required=True)
    digest_weekly.add_argument("--proposals-dir", type=Path, default=None)
    digest_weekly.add_argument("--send-telegram", action="store_true")
    digest_weekly.add_argument("--user-id", type=int, action="append", default=[], dest="user_ids")

    digest_remind = digest_subcommands.add_parser(
        "remind",
        help="Resend the last weekly digest if a snooze is due.",
    )
    _add_state_db_arg(digest_remind)
    digest_remind.add_argument("--send-telegram", action="store_true")
    digest_remind.add_argument("--user-id", type=int, action="append", default=[], dest="user_ids")
    digest_reply = digest_subcommands.add_parser(
        "reply",
        help="Send a user approval/modification reply for the latest digest back through Claude.",
    )
    _add_state_db_arg(digest_reply)
    digest_reply.add_argument("--text", required=True)
    digest_reply.add_argument("--proposals-dir", type=Path, default=None)
    digest_reply.add_argument("--send-telegram", action="store_true")
    digest_reply.add_argument("--user-id", type=int, action="append", default=[], dest="user_ids")

    args = parser.parse_args(argv)
    if args.command == "dry-run":
        mappings = _topic_map_for_dry_run(args.state_db, args.map)
        result = dry_run_update(args.update_json, args.kb_root, mappings)
        for url in result.urls_written:
            print(f"{result.thread_id} -> {result.topic_slug}: {url}")
        return
    if args.command == "state" and args.state_command == "init":
        store = StateStore(args.state_db)
        store.initialize()
        print(f"initialized state DB: {store.path}")
        return
    if args.command == "state" and args.state_command == "bootstrap-seen":
        store = StateStore(args.state_db)
        result = bootstrap_seen_urls(store, args.kb_root)
        print(
            "scanned={scanned} inserted={inserted} duplicates={duplicates} skipped={skipped}".format(
                scanned=result.scanned,
                inserted=result.inserted,
                duplicates=result.duplicates,
                skipped=len(result.skipped),
            )
        )
        for skipped in result.skipped:
            print(f"skipped\t{skipped}")
        return
    if args.command == "topic-map":
        _run_topic_map(args)
        return
    if args.command == "user":
        _run_user(args)
        return
    if args.command == "outbox":
        _run_outbox(args)
        return
    if args.command == "triage":
        _run_triage(args)
        return
    if args.command == "review":
        _run_review(args)
        return
    if args.command == "digest":
        _run_digest(args)
        return
    if args.command == "poll":
        config = load_config(args.config) if args.config is not None else load_config()
        _configure_logging(config.log_level)
        if config.allowed_chat_id == 0:
            raise SystemExit("allowed_chat_id must be configured before polling.")
        state_db = args.state_db or config.state_dir / "state.sqlite"
        store = StateStore(state_db)
        store.initialize()
        for user_id in config.allowed_users:
            store.allow_user(user_id)
        LOGGER.info("Starting Telegram polling for chat %s with state DB %s", config.allowed_chat_id, state_db)
        run_polling(
            bot_token=config.telegram_bot_token,
            store=store,
            allowed_chat_id=config.allowed_chat_id,
            kb_root=config.kb_root,
            auto_approve_lanes=config.auto_approve_lanes,
        )
        return

    config = load_config()
    _configure_logging(config.log_level)
    LOGGER.info("kb-feed scaffold loaded for KB root %s", config.kb_root)
    raise SystemExit("Telegram runtime is not implemented yet; core helpers are scaffolded.")


def _add_state_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB, help=f"SQLite DB path; default: {DEFAULT_STATE_DB}")


def _topic_map_for_dry_run(state_db: Path | None, map_values: list[str]) -> dict[int, str]:
    mappings: dict[int, str] = {}
    if state_db is not None:
        store = StateStore(state_db)
        mappings.update({item.thread_id: item.topic_slug for item in store.list_topic_mappings()})
    mappings.update(parse_topic_map(map_values))
    return mappings


def _run_topic_map(args: argparse.Namespace) -> None:
    store = StateStore(args.state_db)
    if args.topic_map_command == "set":
        store.set_topic_mapping(args.thread_id, args.topic_slug)
        print(f"{args.thread_id} -> {args.topic_slug}")
        return
    if args.topic_map_command == "list":
        for item in store.list_topic_mappings():
            print(f"{item.thread_id}\t{item.topic_slug}")
        return
    if args.topic_map_command == "remove":
        removed = store.remove_topic_mapping(args.thread_id)
        print("removed" if removed else "not found")
        return
    raise SystemExit("topic-map subcommand is required.")


def _run_user(args: argparse.Namespace) -> None:
    store = StateStore(args.state_db)
    if args.user_command == "allow":
        store.allow_user(args.user_id)
        print(f"allowed {args.user_id}")
        return
    if args.user_command == "revoke":
        removed = store.revoke_user(args.user_id)
        print("revoked" if removed else "not found")
        return
    if args.user_command == "list":
        for user_id in store.list_allowed_users():
            print(user_id)
        return
    raise SystemExit("user subcommand is required.")


def _run_outbox(args: argparse.Namespace) -> None:
    store = StateStore(args.state_db)
    if args.outbox_command == "list":
        for row in store.list_outbox(status=args.status):
            print(f"{row.id}\t{row.status}\t{row.thread_id}\t{row.topic_slug}\t{row.fetch_url}")
        return
    if args.outbox_command == "retry":
        retried = store.retry_failed_outbox(tuple(args.row_ids))
        print(f"retried={retried}")
        return
    if args.outbox_command == "flush":
        result = flush_outbox(
            store,
            args.kb_root,
            allow_live_kb=args.allow_live_kb,
            limit=args.limit,
            lock_path=args.lock_path,
            commit=args.commit or args.force_commit,
            force_commit=args.force_commit,
            commit_debounce_seconds=args.commit_debounce_seconds,
            commit_max_pending=args.commit_max_pending,
            git_user_name=args.git_user_name,
            git_user_email=args.git_user_email,
        )
        parts = [
            f"written={len(result.written)}",
            f"failed={len(result.failed)}",
            f"recovered={result.recovered}",
            f"capture_written={len(result.capture_written)}",
            f"capture_failed={len(result.capture_failed)}",
            f"capture_recovered={result.capture_recovered}",
        ]
        if result.commit is not None:
            if result.commit.committed:
                parts.append(f"commit={result.commit.commit_hash}")
            else:
                parts.append(f"commit=skipped:{result.commit.skipped_reason}")
        print(" ".join(parts))
        return
    raise SystemExit("outbox subcommand is required.")


def _run_triage(args: argparse.Namespace) -> None:
    store = StateStore(args.state_db)
    proposals_dir = (args.proposals_dir or store.path.parent / "proposals").expanduser()

    try:
        llm_config = load_llm_config_from_env()
    except LlmUnavailable as exc:
        print(f"triage skipped: {exc}")
        raise SystemExit(2)

    client = LlmClient(llm_config)
    if args.triage_command == "links":
        report = triage_links(
            store,
            args.kb_root,
            client=client,
            proposals_dir=proposals_dir,
            inactivity_hours=args.inactivity_hours,
            limit=args.limit,
            force=args.force,
        )
    elif args.triage_command == "inspiration":
        report = triage_inspiration(
            store,
            args.kb_root,
            client=client,
            proposals_dir=proposals_dir,
            limit=args.limit,
        )
    else:
        raise SystemExit("triage subcommand is required.")

    parts = [
        f"kind={report.kind}",
        f"ran={'yes' if report.ran else 'no'}",
        f"items={report.item_count}",
        f"proposals={report.proposal_count}",
    ]
    if report.proposals_path is not None:
        parts.append(f"file={report.proposals_path}")
    if report.skipped_reason:
        parts.append(f"reason={report.skipped_reason}")
    print(" ".join(parts))


def _run_review(args: argparse.Namespace) -> None:
    kb_root = args.kb_root.expanduser().resolve()
    if kb_root == LIVE_KB_ROOT.resolve() and not args.allow_live_kb:
        raise SystemExit("Review refuses to write to the live KB root without --allow-live-kb.")

    if args.review_command == "assign-link":
        result = assign_link(
            kb_root,
            url=args.url,
            topic_slug=args.topic,
            reason=args.reason,
            add_to_dive_queue=args.dive,
            lock_path=args.lock_path,
        )
    elif args.review_command == "promote-inspiration":
        result = promote_inspiration(
            kb_root,
            contains=args.contains,
            topic_slug=args.topic,
            idea=args.idea,
            lock_path=args.lock_path,
        )
    else:
        raise SystemExit("review subcommand is required.")

    print(
        " ".join(
            [
                f"topic={result.topic_slug}",
                f"reading_list={'yes' if result.added_reading_list else 'no'}",
                f"dive_queue={'yes' if result.added_dive_queue else 'no'}",
                f"marked_source={'yes' if result.marked_source else 'no'}",
            ]
        )
    )


def _run_digest(args: argparse.Namespace) -> None:
    store = StateStore(args.state_db)
    proposals_dir = (getattr(args, "proposals_dir", None) or store.path.parent / "proposals").expanduser()

    if args.digest_command == "weekly":
        report = weekly_digest(
            store,
            args.kb_root,
            agent=ClaudeCliDigestAgent(),
            proposals_dir=proposals_dir,
        )
        sent = 0
        if args.send_telegram:
            config = load_config()
            sent = send_digest_to_users(
                bot_token=config.telegram_bot_token,
                user_ids=_digest_user_ids(args, store, config.allowed_users),
                text=report.telegram_text,
            )
        parts = [
            "kind=weekly",
            f"ran={'yes' if report.ran else 'no'}",
            f"items={report.item_count}",
        ]
        if report.proposals_path is not None:
            parts.append(f"file={report.proposals_path}")
        if sent:
            parts.append(f"telegram_sent={sent}")
        if report.skipped_reason:
            parts.append(f"reason={report.skipped_reason}")
        print(" ".join(parts))
        return

    if args.digest_command == "remind":
        sent = 0
        config = load_config()
        user_ids = _digest_user_ids(args, store, config.allowed_users)
        report = remind_due_digest(
            store,
            bot_token=config.telegram_bot_token,
            user_ids=user_ids if args.send_telegram else (),
        )
        if report.ran and args.send_telegram:
            sent = len(user_ids)
        parts = [
            "kind=remind",
            f"ran={'yes' if report.ran else 'no'}",
        ]
        if sent:
            parts.append(f"telegram_sent={sent}")
        if report.skipped_reason:
            parts.append(f"reason={report.skipped_reason}")
        print(" ".join(parts))
        return

    if args.digest_command == "reply":
        report = process_digest_reply(
            store,
            decision_text=args.text,
            agent=ClaudeCliDigestAgent(),
            proposals_dir=proposals_dir,
        )
        sent = 0
        if args.send_telegram:
            config = load_config()
            sent = send_digest_to_users(
                bot_token=config.telegram_bot_token,
                user_ids=_digest_user_ids(args, store, config.allowed_users),
                text=report.telegram_text,
            )
        parts = [
            "kind=reply",
            f"ran={'yes' if report.ran else 'no'}",
        ]
        if report.proposals_path is not None:
            parts.append(f"file={report.proposals_path}")
        if sent:
            parts.append(f"telegram_sent={sent}")
        print(" ".join(parts))
        return

    raise SystemExit("digest subcommand is required.")


def _digest_user_ids(args: argparse.Namespace, store: StateStore, config_users: tuple[int, ...]) -> tuple[int, ...]:
    if args.user_ids:
        return tuple(args.user_ids)
    users = store.list_allowed_users()
    if users:
        return users
    return config_users
