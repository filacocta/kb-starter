from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from kb_feed.errors import KbFeedError


TelegramTransport = Callable[[str, bytes, int], dict[str, Any]]


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: int,
    text: str,
    transport: TelegramTransport | None = None,
    timeout: int = 30,
) -> None:
    if not bot_token.strip():
        raise KbFeedError("TELEGRAM_BOT_TOKEN is required to send Telegram messages.")
    chunks = _chunks(text, limit=3900)
    sender = transport or _post_form
    for chunk in chunks:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode("utf-8")
        response = sender(url, body, timeout)
        if not bool(response.get("ok")):
            raise KbFeedError(f"Telegram sendMessage failed: {response}")


def _post_form(url: str, body: bytes, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise KbFeedError(f"Telegram HTTP {exc.code} error: {detail}") from exc
    except urllib.error.URLError as exc:
        raise KbFeedError(f"Telegram request failed: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KbFeedError(f"Telegram returned non-JSON body: {raw[:200]}") from exc


def _chunks(text: str, *, limit: int) -> list[str]:
    value = text.strip() or "(empty message)"
    chunks: list[str] = []
    while len(value) > limit:
        split_at = value.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(value[:split_at].rstrip())
        value = value[split_at:].lstrip()
    chunks.append(value)
    return chunks
