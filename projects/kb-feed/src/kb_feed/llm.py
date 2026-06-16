from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from kb_feed.errors import KbFeedError


LOGGER = logging.getLogger(__name__)

DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 60


class LlmUnavailable(KbFeedError):
    """Raised when LLM triage cannot run because credentials or config are missing."""


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    api_base: str
    api_key: str
    model: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def load_llm_config_from_env(env: dict[str, str] | None = None) -> LlmConfig:
    """Return an LlmConfig from environment variables.

    Raises LlmUnavailable when no API key is set so callers can fail gracefully
    instead of crashing the timer-driven triage job.
    """
    source = env if env is not None else dict(os.environ)
    provider = source.get("KB_FEED_LLM_PROVIDER", "deepseek").strip().lower() or "deepseek"

    if provider == "deepseek":
        default_base = DEFAULT_DEEPSEEK_BASE
        default_model = DEFAULT_DEEPSEEK_MODEL
        default_key = source.get("DEEPSEEK_API_KEY", "").strip()
    else:
        default_base = ""
        default_model = ""
        default_key = ""

    api_base = source.get("KB_FEED_LLM_API_BASE", "").strip() or default_base
    model = source.get("KB_FEED_LLM_MODEL", "").strip() or default_model
    api_key = source.get("KB_FEED_LLM_API_KEY", "").strip() or default_key

    if not api_key:
        raise LlmUnavailable(
            "No LLM API key configured. Set KB_FEED_LLM_API_KEY (or DEEPSEEK_API_KEY for the deepseek provider)."
        )
    if not api_base:
        raise LlmUnavailable("KB_FEED_LLM_API_BASE is required when KB_FEED_LLM_PROVIDER is not 'deepseek'.")
    if not model:
        raise LlmUnavailable("KB_FEED_LLM_MODEL is required when KB_FEED_LLM_PROVIDER is not 'deepseek'.")

    try:
        timeout = int(source.get("KB_FEED_LLM_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    return LlmConfig(
        provider=provider,
        api_base=api_base.rstrip("/"),
        api_key=api_key,
        model=model,
        timeout_seconds=timeout,
    )


# Transport is injectable so tests can run without network access.
Transport = Callable[[str, dict[str, str], bytes, int], dict[str, Any]]


def http_post_json(url: str, headers: dict[str, str], body: bytes, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise KbFeedError(f"LLM HTTP {exc.code} error: {detail}") from exc
    except urllib.error.URLError as exc:
        raise KbFeedError(f"LLM request failed: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KbFeedError(f"LLM returned non-JSON body: {raw[:200]}") from exc


class LlmClient:
    """Thin chat-completions client compatible with OpenAI-style HTTP APIs.

    DeepSeek v4 uses the same /chat/completions shape, so the same client
    works for both providers.
    """

    def __init__(self, config: LlmConfig, transport: Transport = http_post_json) -> None:
        self._config = config
        self._transport = transport

    @property
    def config(self) -> LlmConfig:
        return self._config

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Send one chat completion that must return a JSON object."""
        url = f"{self._config.api_base}/chat/completions"
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }
        body = json.dumps(payload).encode("utf-8")
        LOGGER.info(
            "llm-call provider=%s model=%s prompt_chars=%d",
            self._config.provider,
            self._config.model,
            len(system_prompt) + len(user_prompt),
        )
        response = self._transport(url, headers, body, self._config.timeout_seconds)
        return _extract_json_payload(response)


def _extract_json_payload(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise KbFeedError("LLM response had no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise KbFeedError("LLM response had empty content.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise KbFeedError(f"LLM content was not JSON: {content[:200]}") from exc
    if not isinstance(parsed, dict):
        raise KbFeedError("LLM JSON content was not an object.")
    return parsed


def normalize_assignments(
    payload: dict[str, Any],
    allowed_topics: Sequence[str],
) -> list[dict[str, Any]]:
    """Validate LLM topic-assignment proposals.

    Drops any item that names a topic outside ``allowed_topics`` so we never
    propose creating new KB topics.
    """
    raw = payload.get("assignments")
    if not isinstance(raw, list):
        return []
    allowed = set(allowed_topics)
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", "")).strip()
        topic = str(entry.get("topic", "")).strip().lower()
        if not url or not topic:
            continue
        confidence = entry.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        if topic == "skip":
            cleaned.append(
                {
                    "url": url,
                    "topic": "skip",
                    "confidence": confidence_value,
                    "rationale": str(entry.get("rationale", "")).strip() or None,
                }
            )
            continue
        if topic not in allowed:
            cleaned.append(
                {
                    "url": url,
                    "topic": "unknown",
                    "confidence": confidence_value,
                    "rationale": (
                        f"LLM proposed topic '{topic}' which is not an existing KB topic; "
                        "kb-feed will not create new topics."
                    ),
                }
            )
            continue
        cleaned.append(
            {
                "url": url,
                "topic": topic,
                "confidence": confidence_value,
                "rationale": str(entry.get("rationale", "")).strip() or None,
            }
        )
    return cleaned


def normalize_inspiration_proposals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate LLM inspiration-capture proposals.

    The LLM may suggest research questions, candidate sources to search for, or
    related KB topic slugs. We coerce all fields to strings so the proposal
    markdown is deterministic.
    """
    raw = payload.get("proposals")
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        note = str(entry.get("note", "")).strip()
        if not note:
            continue
        questions = entry.get("research_questions") or []
        if not isinstance(questions, list):
            questions = []
        candidate_topics = entry.get("candidate_topics") or []
        if not isinstance(candidate_topics, list):
            candidate_topics = []
        cleaned.append(
            {
                "note": note,
                "research_questions": [str(q).strip() for q in questions if str(q).strip()],
                "candidate_topics": [str(t).strip().lower() for t in candidate_topics if str(t).strip()],
                "rationale": str(entry.get("rationale", "")).strip() or None,
            }
        )
    return cleaned
