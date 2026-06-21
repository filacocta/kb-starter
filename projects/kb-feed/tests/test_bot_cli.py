from __future__ import annotations

import json
import logging
from datetime import date

from kb_feed.bot import _configure_logging, main
from kb_feed.state import StateStore


def _kb_fixture(tmp_path):
    kb_root = tmp_path / "kb"
    topic_dir = kb_root / "topics" / "obsidian"
    topic_dir.mkdir(parents=True)
    readinglist_dir = kb_root / "readinglist"
    readinglist_dir.mkdir(parents=True)
    (topic_dir / "reading-list.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Obsidian Reading List\n\n"
        "## Queue\n",
        encoding="utf-8",
    )
    (kb_root / "_dive-queue.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Dive Queue\n\n"
        "## obsidian\n",
        encoding="utf-8",
    )
    (readinglist_dir / "Links.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# URL Inbox\n\n"
        "- [ ] https://example.com/unfiltered — (via Telegram #unfiltered, 2026-05-31)\n",
        encoding="utf-8",
    )
    (readinglist_dir / "topics.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "modified: 2026-05-01\n"
        "type: meta\n"
        "---\n\n"
        "# Topic Interests\n\n"
        "- [ ] compare graph-based notes and field notebooks — (via Telegram #inspiration-capture, 2026-05-31)\n",
        encoding="utf-8",
    )
    return kb_root


def _write_update(tmp_path):
    update_path = tmp_path / "update.json"
    update_path.write_text(
        json.dumps(
            {
                "message": {
                    "message_thread_id": 123,
                    "text": "Read this",
                    "entities": [
                        {
                            "type": "text_link",
                            "offset": 0,
                            "length": 4,
                            "url": "https://www.example.com/path/?utm_source=telegram&a=1#frag",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return update_path


def _resolver(*_args):
    return [(None, None, None, None, ("93.184.216.34", 0))]


def test_state_and_topic_map_cli(tmp_path, capsys) -> None:
    state_db = tmp_path / "state.sqlite"

    main(["state", "init", "--state-db", str(state_db)])
    main(["topic-map", "set", "--state-db", str(state_db), "123", "obsidian"])
    main(["topic-map", "list", "--state-db", str(state_db)])

    output = capsys.readouterr().out
    assert f"initialized state DB: {state_db}" in output
    assert "123 -> obsidian" in output
    assert "123\tobsidian" in output


def test_configure_logging_quiets_http_client_logs() -> None:
    _configure_logging("INFO")

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_state_bootstrap_seen_cli(tmp_path, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)
    reading_list = kb_root / "topics" / "obsidian" / "reading-list.md"
    reading_list.write_text(
        reading_list.read_text(encoding="utf-8") + "- [ ] https://example.com/a — saved\n",
        encoding="utf-8",
    )

    main(["state", "init", "--state-db", str(state_db)])
    main(["state", "bootstrap-seen", "--state-db", str(state_db), "--kb-root", str(kb_root)])

    output = capsys.readouterr().out
    assert "scanned=1 inserted=1 duplicates=0 skipped=0" in output
    assert StateStore(state_db).has_seen_url("https://example.com/a")


def test_user_cli(tmp_path, capsys) -> None:
    state_db = tmp_path / "state.sqlite"

    main(["state", "init", "--state-db", str(state_db)])
    main(["user", "allow", "--state-db", str(state_db), "111"])
    main(["user", "list", "--state-db", str(state_db)])

    output = capsys.readouterr().out
    assert "allowed 111" in output
    assert "\n111\n" in output


def test_dry_run_cli_reads_topic_map_from_state(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("kb_feed.dry_run.date", _FixedDate)
    monkeypatch.setattr("kb_feed.dry_run.socket.getaddrinfo", _resolver)
    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)
    update_path = _write_update(tmp_path)

    main(["state", "init", "--state-db", str(state_db)])
    main(["topic-map", "set", "--state-db", str(state_db), "123", "obsidian"])
    main(["dry-run", str(update_path), "--kb-root", str(kb_root), "--state-db", str(state_db)])

    output = capsys.readouterr().out
    assert "123 -> obsidian: https://example.com/path?a=1" in output
    line = "- [ ] https://example.com/path?a=1 — Read this — (via Telegram #obsidian, 2026-05-28)"
    assert line in (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert line not in (kb_root / "_dive-queue.md").read_text(encoding="utf-8")


def test_poll_cli_initializes_state_and_seeds_config_users(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "kb-feed.toml"
    state_db = tmp_path / "runtime.sqlite"
    calls = []
    config_path.write_text(
        'kb_root = "/tmp/kb"\n'
        'state_dir = "/tmp/unused"\n'
        "allowed_chat_id = -100123\n"
        "allowed_users = [111]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

    def fake_run_polling(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("kb_feed.bot.run_polling", fake_run_polling)

    main(["poll", "--config", str(config_path), "--state-db", str(state_db)])

    assert calls[0]["bot_token"] == "token"
    assert calls[0]["allowed_chat_id"] == -100123
    assert calls[0]["store"].path == state_db
    assert calls[0]["store"].is_user_allowed(111)
    assert calls[0]["auto_approve_lanes"] == ()


def test_outbox_flush_cli_writes_fixture(tmp_path, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)

    main(["state", "init", "--state-db", str(state_db)])
    store = StateStore(state_db)
    store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    main(["outbox", "flush", "--state-db", str(state_db), "--kb-root", str(kb_root)])
    output = capsys.readouterr().out

    assert "written=1 failed=0 recovered=0" in output
    assert StateStore(state_db).list_outbox()[0].status == "written"


def test_outbox_retry_cli_moves_failed_rows_to_pending(tmp_path, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    main(["state", "init", "--state-db", str(state_db)])
    store = StateStore(state_db)
    row_id = store.enqueue_url(
        thread_id=123,
        topic_slug="obsidian",
        original_url="https://example.com/a",
        normalized_url="https://example.com/a",
        fetch_url="https://example.com/a",
    )
    store.mark_outbox_failed(row_id, "boom")

    main(["outbox", "retry", "--state-db", str(state_db), "--id", str(row_id)])

    output = capsys.readouterr().out
    assert "retried=1" in output
    assert StateStore(state_db).list_outbox()[0].status == "pending"


def test_review_assign_link_cli_writes_topic_queue_and_marks_inbox(tmp_path, capsys) -> None:
    kb_root = _kb_fixture(tmp_path)

    main(
        [
            "review",
            "assign-link",
            "--kb-root",
            str(kb_root),
            "--url",
            "https://example.com/unfiltered",
            "--topic",
            "obsidian",
            "--reason",
            "belongs with note tooling",
        ]
    )

    output = capsys.readouterr().out
    assert "topic=obsidian reading_list=yes research_idea=no marked_source=yes" in output
    reading_list = (kb_root / "topics" / "obsidian" / "reading-list.md").read_text(encoding="utf-8")
    assert "- [ ] https://example.com/unfiltered — belongs with note tooling" in reading_list
    inbox = (kb_root / "readinglist" / "Links.md").read_text(encoding="utf-8")
    assert "assigned to obsidian" in inbox


def test_review_live_gate_requires_explicit_flag(tmp_path, monkeypatch) -> None:
    kb_root = _kb_fixture(tmp_path)
    monkeypatch.setattr("kb_feed.bot.LIVE_KB_ROOT", kb_root)

    with __import__("pytest").raises(SystemExit, match="without --allow-live-kb"):
        main(
            [
                "review",
                "assign-link",
                "--kb-root",
                str(kb_root),
                "--url",
                "https://example.com/unfiltered",
                "--topic",
                "obsidian",
            ]
        )


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 28)


def test_triage_links_cli_exits_2_without_api_key(tmp_path, monkeypatch, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)
    main(["state", "init", "--state-db", str(state_db)])

    for var in ("DEEPSEEK_API_KEY", "KB_FEED_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    with __import__("pytest").raises(SystemExit) as exc_info:
        main(
            [
                "triage",
                "links",
                "--state-db",
                str(state_db),
                "--kb-root",
                str(kb_root),
                "--inactivity-hours",
                "0",
            ]
        )

    assert exc_info.value.code == 2
    assert "triage skipped" in capsys.readouterr().out


def test_triage_links_cli_writes_proposal_when_credentials_present(tmp_path, monkeypatch, capsys) -> None:
    import json as _json

    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)
    (kb_root / "readinglist" / "Links.md").write_text(
        "---\nauthor: kb-bot\n---\n# URL Inbox\n\n"
        "- [ ] https://example.com/a — (via Telegram #unfiltered, 2026-05-31)\n",
        encoding="utf-8",
    )
    main(["state", "init", "--state-db", str(state_db)])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")

    def fake_transport(url, headers, body, timeout):
        return {
            "choices": [
                {
                    "message": {
                        "content": _json.dumps(
                            {"assignments": [{"url": "https://example.com/a — (via Telegram #unfiltered, 2026-05-31)", "topic": "obsidian"}]}
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("kb_feed.llm.http_post_json", fake_transport)
    monkeypatch.setattr("kb_feed.bot.LlmClient", lambda config: __import__("kb_feed.llm", fromlist=["LlmClient"]).LlmClient(config, transport=fake_transport))

    proposals_dir = tmp_path / "proposals"
    main(
        [
            "triage",
            "links",
            "--state-db",
            str(state_db),
            "--kb-root",
            str(kb_root),
            "--inactivity-hours",
            "0",
            "--proposals-dir",
            str(proposals_dir),
        ]
    )

    output = capsys.readouterr().out
    assert "ran=yes" in output
    assert "proposals=1" in output
    files = list(proposals_dir.iterdir())
    assert files, "expected one proposal file"
    assert "topic: obsidian" in files[0].read_text(encoding="utf-8")


def test_digest_weekly_cli_writes_proposal_from_agent(tmp_path, monkeypatch, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    kb_root = _kb_fixture(tmp_path)
    main(["state", "init", "--state-db", str(state_db)])

    class FakeAgent:
        def complete(self, prompt):
            return "Summary\n- One unfiltered link needs review."

    monkeypatch.setattr("kb_feed.bot.default_digest_agent", FakeAgent)

    proposals_dir = tmp_path / "proposals"
    main(
        [
            "digest",
            "weekly",
            "--state-db",
            str(state_db),
            "--kb-root",
            str(kb_root),
            "--proposals-dir",
            str(proposals_dir),
        ]
    )

    output = capsys.readouterr().out
    assert "kind=weekly ran=yes" in output
    files = list(proposals_dir.iterdir())
    assert files, "expected one weekly digest file"
    assert "One unfiltered link needs review." in files[0].read_text(encoding="utf-8")


def test_digest_reply_cli_writes_agent_followup(tmp_path, monkeypatch, capsys) -> None:
    state_db = tmp_path / "state.sqlite"
    main(["state", "init", "--state-db", str(state_db)])
    store = StateStore(state_db)
    store.set_meta("last_weekly_digest_text", "Weekly digest body")

    class FakeAgent:
        def complete(self, prompt):
            return "Acknowledged\n- I will prepare exact review commands."

    monkeypatch.setattr("kb_feed.bot.default_digest_agent", FakeAgent)
    proposals_dir = tmp_path / "proposals"

    main(
        [
            "digest",
            "reply",
            "--state-db",
            str(state_db),
            "--proposals-dir",
            str(proposals_dir),
            "--text",
            "approve the obsidian move",
        ]
    )

    output = capsys.readouterr().out
    assert "kind=reply ran=yes" in output
    files = list(proposals_dir.iterdir())
    assert files, "expected one digest reply file"
    assert "approve the obsidian move" in files[0].read_text(encoding="utf-8")
