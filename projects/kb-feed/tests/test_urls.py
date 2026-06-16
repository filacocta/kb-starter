from __future__ import annotations

from dataclasses import dataclass

import pytest

from kb_feed.errors import UrlRejected
from kb_feed.security import is_public_address
from kb_feed.urls import extract_urls_from_entities, normalize_url


@dataclass(frozen=True)
class Entity:
    type: str
    offset: int = 0
    length: int = 0
    url: str | None = None


def _resolver(address: str):
    def resolve(*_args):
        return [(None, None, None, None, (address, 0))]

    return resolve


def test_extract_urls_uses_text_link_entity_url() -> None:
    urls = extract_urls_from_entities(
        "interesting link",
        [Entity(type="text_link", url="https://example.com/source")],
    )

    assert urls == ["https://example.com/source"]


def test_extract_urls_allows_telegram_parser_for_url_entities() -> None:
    entity = Entity(type="url", offset=0, length=0)

    urls = extract_urls_from_entities("ignored", [entity], parse_entity=lambda _entity: "https://example.com/a")

    assert urls == ["https://example.com/a"]


def test_normalize_url_strips_tracking_fragment_and_trailing_slash() -> None:
    record = normalize_url(
        "HTTPS://WWW.Example.com/path/?b=2&utm_source=x&a=1#section",
        resolver=_resolver("93.184.216.34"),
    )

    assert record.original_url == "HTTPS://WWW.Example.com/path/?b=2&utm_source=x&a=1#section"
    assert record.canonical_url == "https://example.com/path?b=2&a=1"
    assert record.normalized_url == "https://example.com/path?b=2&a=1"
    assert record.fetch_url == "https://example.com/path?b=2&a=1"


def test_normalize_url_equates_x_and_fxtwitter_for_dedup() -> None:
    x_record = normalize_url(
        "https://x.com/exampleuser/status/1?utm_campaign=test",
        resolver=_resolver("104.244.42.1"),
    )
    fx_record = normalize_url(
        "https://fxtwitter.com/exampleuser/status/1",
        resolver=_resolver("104.244.42.1"),
    )

    assert x_record.normalized_url == fx_record.normalized_url
    assert x_record.fetch_url == "https://fxtwitter.com/exampleuser/status/1"


def test_normalize_url_rejects_private_resolution() -> None:
    with pytest.raises(UrlRejected):
        normalize_url("https://example.com", resolver=_resolver("10.0.0.5"))


def test_public_address_policy_blocks_cloud_metadata_and_carrier_nat() -> None:
    assert not is_public_address("169.254.169.254")
    assert not is_public_address("100.64.1.2")
    assert is_public_address("8.8.8.8")


def test_normalize_url_rejects_non_http_scheme() -> None:
    with pytest.raises(UrlRejected):
        normalize_url("file:///etc/passwd")


def test_extract_urls_fallback_uses_telegram_utf16_offsets() -> None:
    text = "saved 😀 https://example.com/a"
    offset = len("saved 😀 ".encode("utf-16-le")) // 2
    length = len("https://example.com/a".encode("utf-16-le")) // 2

    urls = extract_urls_from_entities(text, [Entity(type="url", offset=offset, length=length)])

    assert urls == ["https://example.com/a"]
