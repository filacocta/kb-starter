from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from kb_feed.state import validate_topic_slug


DEFAULT_CONFIG_PATH = Path("~/.config/kb-feed/kb-feed.toml").expanduser()
DEFAULT_KB_ROOT = Path("~/Vault/knowledgebase").expanduser()


@dataclass(frozen=True)
class Config:
    kb_root: Path
    state_dir: Path
    allowed_chat_id: int
    allowed_users: tuple[int, ...]
    log_level: str
    commit_debounce_seconds: int
    commit_max_pending: int
    git_user_name: str
    git_user_email: str
    telegram_bot_token: str
    auto_approve_lanes: tuple[str, ...] = ()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required in ~/.config/kb-feed/credentials.env.")

    return Config(
        kb_root=_path(data.get("kb_root", str(DEFAULT_KB_ROOT))),
        state_dir=_path(data.get("state_dir", "~/.local/state/kb-feed")),
        allowed_chat_id=int(data.get("allowed_chat_id", 0)),
        allowed_users=tuple(int(user_id) for user_id in data.get("allowed_users", [])),
        log_level=str(data.get("log_level", "INFO")),
        commit_debounce_seconds=int(data.get("commit_debounce_seconds", 300)),
        commit_max_pending=int(data.get("commit_max_pending", 20)),
        git_user_name=str(data.get("git_user_name", "kb-bot")),
        git_user_email=str(data.get("git_user_email", "noreply@kb-feed.local")),
        telegram_bot_token=token,
        auto_approve_lanes=_auto_approve_lanes(data.get("lanes", {})),
    )


def _path(value: object) -> Path:
    return Path(str(value)).expanduser()


def _auto_approve_lanes(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    lanes: list[str] = []
    for raw_slug, raw_config in value.items():
        if not isinstance(raw_config, dict):
            continue
        if bool(raw_config.get("auto_approve", False)):
            lanes.append(validate_topic_slug(str(raw_slug)))
    return tuple(sorted(lanes))
