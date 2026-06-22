from __future__ import annotations

import json

import pytest

from kb_feed.errors import KbFeedError
from kb_feed.llm import (
    DEFAULT_DEEPSEEK_BASE,
    DEFAULT_DEEPSEEK_MODEL,
    LlmClient,
    LlmUnavailable,
    load_llm_config_from_env,
    normalize_assignments,
    normalize_inspiration_proposals,
)


def test_load_llm_config_from_env_defaults_to_deepseek() -> None:
    config = load_llm_config_from_env({"DEEPSEEK_API_KEY": "secret"})

    assert config.provider == "deepseek"
    assert config.api_base == DEFAULT_DEEPSEEK_BASE
    assert config.model == DEFAULT_DEEPSEEK_MODEL
    assert config.api_key == "secret"


def test_load_llm_config_from_env_raises_when_no_key() -> None:
    with pytest.raises(LlmUnavailable):
        load_llm_config_from_env({})


def test_load_llm_config_from_env_kb_feed_key_overrides_provider_default() -> None:
    config = load_llm_config_from_env(
        {
            "KB_FEED_LLM_PROVIDER": "deepseek",
            "KB_FEED_LLM_API_KEY": "override",
            "DEEPSEEK_API_KEY": "ignored",
        }
    )

    assert config.api_key == "override"


def test_load_llm_config_from_env_requires_base_for_custom_provider() -> None:
    with pytest.raises(LlmUnavailable):
        load_llm_config_from_env(
            {
                "KB_FEED_LLM_PROVIDER": "custom",
                "KB_FEED_LLM_API_KEY": "k",
                "KB_FEED_LLM_MODEL": "m",
            }
        )


def test_llm_client_complete_json_extracts_assistant_content() -> None:
    captured: dict = {}

    def fake_transport(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["timeout"] = timeout
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"assignments": [{"url": "https://x", "topic": "obsidian"}]})
                    }
                }
            ]
        }

    config = load_llm_config_from_env({"DEEPSEEK_API_KEY": "secret"})
    client = LlmClient(config, transport=fake_transport)

    result = client.complete_json(system_prompt="sys", user_prompt="user")

    assert result == {"assignments": [{"url": "https://x", "topic": "obsidian"}]}
    assert captured["url"] == f"{DEFAULT_DEEPSEEK_BASE}/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == DEFAULT_DEEPSEEK_MODEL
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_llm_client_complete_json_rejects_non_json_content() -> None:
    def fake_transport(url, headers, body, timeout):
        return {"choices": [{"message": {"content": "this is not JSON"}}]}

    config = load_llm_config_from_env({"DEEPSEEK_API_KEY": "secret"})
    client = LlmClient(config, transport=fake_transport)

    with pytest.raises(KbFeedError):
        client.complete_json(system_prompt="sys", user_prompt="user")


def test_normalize_assignments_filters_to_allowed_topics() -> None:
    payload = {
        "assignments": [
            {"url": "https://a", "topic": "obsidian", "confidence": 0.9, "rationale": "matches"},
            {"url": "https://b", "topic": "made-up", "confidence": 0.4, "rationale": "no"},
            {"url": "https://c", "topic": "skip", "rationale": "off-topic"},
            {"url": "", "topic": "obsidian"},
        ]
    }

    result = normalize_assignments(payload, ["obsidian", "rockhounding"])

    assert len(result) == 3
    assert result[0]["topic"] == "obsidian"
    assert result[0]["confidence"] == 0.9
    assert result[1]["topic"] == "unknown"
    assert "kb-feed will not create new topics" in result[1]["rationale"]
    assert result[2]["topic"] == "skip"


def test_normalize_inspiration_proposals_keeps_only_strings() -> None:
    payload = {
        "proposals": [
            {
                "note": "study X",
                "research_questions": ["why?", "  ", 42],
                "candidate_topics": ["Obsidian", "rockhounding"],
                "rationale": "fits topic ecosystem",
            },
            {"note": "", "research_questions": []},
        ]
    }

    result = normalize_inspiration_proposals(payload)

    assert len(result) == 1
    assert result[0]["note"] == "study X"
    assert result[0]["research_questions"] == ["why?", "42"]
    assert result[0]["candidate_topics"] == ["obsidian", "rockhounding"]
    assert result[0]["rationale"] == "fits topic ecosystem"
