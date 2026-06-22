from __future__ import annotations

from pathlib import Path

import pytest

from kb_feed.config import load_config


def test_load_config_requires_telegram_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        load_config(tmp_path / "missing.toml")


def test_load_config_parses_allowed_users_and_paths(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "kb-feed.toml"
    config_path.write_text(
        'kb_root = "/tmp/kb"\n'
        'state_dir = "~/state"\n'
        "allowed_chat_id = -100123\n"
        "allowed_users = [111, 222]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

    config = load_config(config_path)

    assert config.kb_root == Path("/tmp/kb")
    assert config.state_dir.name == "state"
    assert config.allowed_chat_id == -100123
    assert config.allowed_users == (111, 222)
    assert config.auto_approve_lanes == ()
    assert config.telegram_bot_token == "token"


def test_load_config_parses_auto_approve_lanes(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "kb-feed.toml"
    config_path.write_text(
        'kb_root = "/tmp/kb"\n'
        "allowed_chat_id = -100123\n"
        "[lanes.obsidian]\n"
        "auto_approve = true\n"
        "[lanes.rockhounding]\n"
        "auto_approve = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

    config = load_config(config_path)

    assert config.auto_approve_lanes == ("obsidian",)
