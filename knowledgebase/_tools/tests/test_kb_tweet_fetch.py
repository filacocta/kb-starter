from __future__ import annotations

import hashlib
import importlib.util
import datetime
import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-tweet-fetch.py"


@pytest.fixture(autouse=True)
def deny_network_and_sleep(monkeypatch):
    """Never let a unit test reach the network or wait accidentally."""
    def denied(*_args, **_kwargs):
        raise AssertionError("unexpected network request or sleep in kb-tweet-fetch test")

    monkeypatch.setattr("urllib.request.urlopen", denied)
    monkeypatch.setattr("time.sleep", denied)


def load_tweet_module():
    spec = importlib.util.spec_from_file_location("kb_tweet_fetch", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fake_tweet(tweet_id, screen_name="alice", text=None, replying_to_status=None, quote=None, media=None):
    text = text or f"tweet {tweet_id}"
    numeric_id = int(tweet_id) if str(tweet_id).isdigit() else 0
    return {
        "id": str(tweet_id),
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "text": text,
        "raw_text": {"text": text, "display_text_range": [0, len(text)], "facets": []},
        "author": {"screen_name": screen_name, "name": screen_name.title()},
        "media": media or {"photos": [], "videos": []},
        "quote": quote,
        "replying_to": screen_name if replying_to_status else None,
        "replying_to_status": str(replying_to_status) if replying_to_status else None,
        "created_timestamp": 1_700_000_000 + numeric_id,
    }


class FakeResponse:
    status = 200

    def __init__(self, body: bytes, *, response_url: str | None = None, status: int = 200):
        self.body = body
        self.status = status
        self.response_url = response_url
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        return self.body if size is None else self.body[:size]

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str | None:
        return self.response_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def install_responses(monkeypatch, responses: list[FakeResponse]):
    calls: list[tuple[str, int]] = []

    def fake_urlopen(request, timeout=20):
        calls.append((request.full_url, timeout))
        if not responses:
            raise AssertionError("inventory made an unexpected second endpoint request")
        return responses.pop(0)

    monkeypatch.setattr(sys.modules["kb_tweet_fetch"], "_open_inventory_request", fake_urlopen)
    return calls


def test_quote_tweet_populates_quote_and_video_urls(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    quote = fake_tweet(
        200,
        screen_name="bob",
        text="quoted text",
        media={"photos": [], "videos": [{"url": "https://video.example/quoted.mp4"}]},
    )
    main = fake_tweet(
        100,
        text="main text",
        quote=quote,
        media={
            "photos": [{"url": "https://img.example/1.jpg"}, {"url": "https://img.example/2.jpg"}],
            "videos": [],
        },
    )

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", lambda tweet_id: tweet_fetch.TweetFetch(main))
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([main], None))

    code = tweet_fetch.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["main"]["text"] == "main text"
    assert payload["main"]["photos"] == ["https://img.example/1.jpg", "https://img.example/2.jpg"]
    assert payload["quote"]["text"] == "quoted text"
    assert payload["video_urls"] == ["https://video.example/quoted.mp4"]
    assert payload["videos_by_context"] == {
        "main": [],
        "quote": ["https://video.example/quoted.mp4"],
        "same_author_thread": [],
        "self_quote_chain": [],
    }


def test_json_is_default_when_no_out_is_provided(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    main = fake_tweet(100, text="main text")

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", lambda tweet_id: tweet_fetch.TweetFetch(main))
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([main], None))

    code = tweet_fetch.main(["https://x.com/alice/status/100"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["main"]["text"] == "main text"


def test_thread_endpoint_keeps_focal_author_posts_in_order(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    main = fake_tweet(1, text="1/ root")
    thread = [
        main,
        fake_tweet(2, text="2/ reply one"),
        fake_tweet(3, screen_name="bob", text="bob reply"),
        fake_tweet(4, text="3/ reply two"),
    ]

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", lambda tweet_id: tweet_fetch.TweetFetch(main))
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: (thread, None))

    code = tweet_fetch.main(["https://twitter.com/alice/status/1", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert [entry["id"] for entry in payload["thread"]] == ["1", "2", "4"]
    assert [entry["text"] for entry in payload["thread"]] == ["1/ root", "2/ reply one", "3/ reply two"]
    assert payload["flags"]["is_thread"] is True
    assert payload["flags"]["thread_error"] is False


def test_thread_endpoint_error_falls_back_to_main(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    main = fake_tweet(10, text="1/ A useful thread")

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", lambda tweet_id: tweet_fetch.TweetFetch(main))
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([], "boom"))

    code = tweet_fetch.main(["https://fxtwitter.com/alice/status/10", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert [entry["id"] for entry in payload["thread"]] == ["10"]
    assert payload["flags"]["looks_like_thread_start"] is True
    assert payload["flags"]["is_thread"] is False
    assert payload["flags"]["thread_error"] is True
    assert payload["warnings"] == ["boom"]


def test_self_quote_chain_refetches_each_quote(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    q2 = fake_tweet("Q2", text="oldest quoted self tweet")
    q1 = fake_tweet("Q1", text="middle quoted self tweet", quote={
        "id": "Q2",
        "author": {"screen_name": "alice", "name": "Alice"},
        "raw_text": {"text": "embedded Q2 text"},
    })
    main = fake_tweet(100, text="newest self quote", quote={
        "id": "Q1",
        "author": {"screen_name": "alice", "name": "Alice"},
        "raw_text": {"text": "embedded Q1 text"},
    })

    def fake_fetch(tweet_id):
        return tweet_fetch.TweetFetch({"100": main, "Q1": q1, "Q2": q2}[str(tweet_id)])

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", fake_fetch)
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([main], None))

    code = tweet_fetch.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert [entry["id"] for entry in payload["quote_chain"]] == ["Q1", "Q2"]
    assert payload["flags"]["quote_chain_len"] == 2


def test_foreign_quote_keeps_one_level_quote_without_chain(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    quote = fake_tweet("X", screen_name="bob", text="foreign quote")
    main = fake_tweet(100, text="main with foreign quote", quote=quote)

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", lambda tweet_id: tweet_fetch.TweetFetch(main))
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([main], None))

    code = tweet_fetch.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["quote_chain"] == []
    assert payload["quote"]["id"] == "X"
    assert payload["quote"]["text"] == "foreign quote"


def test_videos_by_context_keeps_thread_and_self_quote_locations(monkeypatch, capsys):
    tweet_fetch = load_tweet_module()
    q2 = fake_tweet(
        "Q2",
        text="oldest quoted self tweet",
        media={"photos": [], "videos": [{"url": "https://video.example/q2.mp4"}]},
    )
    q1 = fake_tweet(
        "Q1",
        text="middle quoted self tweet",
        quote={"id": "Q2", "author": {"screen_name": "alice", "name": "Alice"}},
        media={"photos": [], "videos": [{"url": "https://video.example/q1.mp4"}]},
    )
    main = fake_tweet(
        100,
        text="newest self quote",
        quote={
            "id": "Q1",
            "author": {"screen_name": "alice", "name": "Alice"},
            "media": {"photos": [], "videos": [{"url": "https://video.example/q1-embedded.mp4"}]},
        },
        media={"photos": [], "videos": [{"url": "https://video.example/main.mp4"}]},
    )
    thread_reply = fake_tweet(
        101,
        text="thread video",
        media={"photos": [], "videos": [{"url": "https://video.example/thread.mp4"}]},
    )

    def fake_fetch(tweet_id):
        return tweet_fetch.TweetFetch({"100": main, "Q1": q1, "Q2": q2}[str(tweet_id)])

    monkeypatch.setattr(tweet_fetch, "fetch_tweet", fake_fetch)
    monkeypatch.setattr(tweet_fetch, "fetch_thread", lambda tweet_id: ([main, thread_reply], None))

    code = tweet_fetch.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["video_urls"] == [
        "https://video.example/main.mp4",
        "https://video.example/thread.mp4",
        "https://video.example/q1.mp4",
        "https://video.example/q2.mp4",
    ]
    assert payload["videos_by_context"] == {
        "main": ["https://video.example/main.mp4"],
        "quote": [],
        "same_author_thread": ["https://video.example/thread.mp4"],
        "self_quote_chain": ["https://video.example/q1.mp4", "https://video.example/q2.mp4"],
    }


def test_real_fxtwitter_shapes_raw_text_dict_and_media_all_video():
    """Regression: real fxtwitter returns raw_text as a dict and videos under media.all."""
    m = load_tweet_module()
    tweet = {
        "id": "1", "url": "https://x.com/bob/status/1",
        "text": "plain string text",
        "raw_text": {"text": "full raw text", "display_text_range": [0, 13], "facets": []},
        "author": {"screen_name": "bob", "name": "Bob"},
        "media": {
            "all": [
                {"type": "photo", "url": "https://pbs.twimg.com/p.jpg"},
                {"type": "video", "url": "https://video.twimg.com/v.mp4"},
            ],
            "photos": [{"url": "https://pbs.twimg.com/p.jpg"}],
        },
    }
    assert m.tweet_text(tweet) == "full raw text"
    assert m.media_videos(tweet) == ["https://video.twimg.com/v.mp4"]
    compact = m.compact_tweet(tweet)
    assert isinstance(compact["text"], str)           # regex-safe (was a dict → crash)
    assert compact["videos"] == ["https://video.twimg.com/v.mp4"]
    assert m.looks_like_thread_start(compact["text"]) in (True, False)  # must not raise


def test_article_to_markdown_renders_title_styles_and_links():
    m = load_tweet_module()
    article = {
        "title": "Article title",
        "content": {
            "entityMap": {
                "0": {
                    "type": "LINK",
                    "mutability": "MUTABLE",
                    "data": {"url": "https://example.com/read"},
                }
            },
            "blocks": [
                {"type": "header-two", "text": "Section"},
                {
                    "type": "unstyled",
                    "text": "Bold words",
                    "inlineStyleRanges": [{"offset": 0, "length": 4, "style": "BOLD"}],
                },
                {
                    "type": "unstyled",
                    "text": "linked text",
                    "entityRanges": [{"offset": 7, "length": 4, "key": 0}],
                },
            ],
        },
    }

    markdown = m.article_to_markdown(article)

    assert markdown is not None
    assert "# Article title" in markdown
    assert "## Section" in markdown
    assert "**Bold** words" in markdown
    assert "linked [text](https://example.com/read)" in markdown


def test_article_to_markdown_returns_none_for_empty_or_missing_blocks():
    m = load_tweet_module()

    assert m.article_to_markdown({"title": "Only title", "content": {"blocks": []}}) is None
    assert m.article_to_markdown({"title": "Only title"}) is None


def test_compact_tweet_populates_article_markdown():
    m = load_tweet_module()
    tweet = fake_tweet(
        42,
        text="",
    )
    tweet["article"] = {
        "title": "Compact article",
        "content": {"blocks": [{"type": "unstyled", "text": "Body text"}]},
    }

    compact = m.compact_tweet(tweet)

    assert compact["article_title"] == "Compact article"
    assert compact["article_markdown"] == "# Compact article\n\nBody text"


def test_resolve_article_status_id_uses_injected_redirect_resolver():
    m = load_tweet_module()

    article_id = m.resolve_article_status_id(
        "t.co/abc",
        follow_redirects=lambda url: "https://x.com/i/article/1805586901141012870",
    )

    assert article_id == "1805586901141012870"
    assert m.resolve_article_status_id("x.com/i/article/12345") == "12345"
    assert m.resolve_article_status_id(
        "https://t.co/abc",
        follow_redirects=lambda url: "https://x.com/alice/status/123",
    ) is None


def test_analyze_resolves_bare_article_links_for_main_and_quote(monkeypatch, capsys):
    m = load_tweet_module()
    quote = fake_tweet(200, screen_name="bob", text="https://t.co/quote")
    main = fake_tweet(100, text="https://t.co/main", quote=quote)
    article_main = fake_tweet(300, text="")
    article_main["article"] = {
        "title": "Main article",
        "content": {"blocks": [{"type": "unstyled", "text": "Main body"}]},
    }
    article_quote = fake_tweet(400, text="")
    article_quote["article"] = {
        "title": "Quote article",
        "content": {"blocks": [{"type": "unstyled", "text": "Quote body"}]},
    }

    def fake_fetch(tweet_id):
        return m.TweetFetch({"100": main, "300": article_main, "400": article_quote}[str(tweet_id)])

    monkeypatch.setattr(m, "fetch_tweet", fake_fetch)
    monkeypatch.setattr(m, "fetch_thread", lambda tweet_id: ([main], None))
    monkeypatch.setattr(
        m,
        "resolve_article_status_id",
        lambda url: {"https://t.co/main": "300", "https://t.co/quote": "400"}.get(url),
    )

    code = m.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["main"]["article_title"] == "Main article"
    assert "Main body" in payload["main"]["article_markdown"]
    assert payload["quote"]["article_title"] == "Quote article"
    assert "Quote body" in payload["quote"]["article_markdown"]


def test_fetch_tweet_retries_then_succeeds(monkeypatch):
    """Retry+backoff: a transient failure is retried, then succeeds (no real network/sleep)."""
    m = load_tweet_module()
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    valid_body = json.dumps({"code": 200, "tweet": {
        "id": "1", "text": "hi", "raw_text": {"text": "hi"},
        "author": {"screen_name": "a", "name": "A"}, "media": {},
    }})

    class FakeResp:
        status = 200
        def __init__(self, body): self._b = body.encode("utf-8")
        def read(self): return self._b
        def getcode(self): return 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    calls = {"n": 0}
    def fake_urlopen(req, timeout=20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise m.urllib.error.URLError("transient boom")
        return FakeResp(valid_body)

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    result = m.fetch_tweet("1")
    assert calls["n"] == 2            # retried exactly once before success
    assert result.tweet is not None and result.tweet["text"] == "hi"


def test_fetch_tweet_retries_on_empty_200(monkeypatch):
    """200-but-empty (the fxtwitter flaky case) is retried, then succeeds."""
    m = load_tweet_module()
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    empty = json.dumps({"code": 200, "tweet": {}})
    good = json.dumps({"code": 200, "tweet": {"id": "1", "text": "ok", "author": {"screen_name": "a", "name": "A"}, "media": {}}})

    class FakeResp:
        status = 200
        def __init__(self, body): self._b = body.encode("utf-8")
        def read(self): return self._b
        def getcode(self): return 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    bodies = [empty, good]
    def fake_urlopen(req, timeout=20):
        return FakeResp(bodies.pop(0))

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    result = m.fetch_tweet("1")
    assert result.tweet is not None and result.tweet["text"] == "ok"


def test_fetch_tweet_accepts_v2_status_key(monkeypatch):
    """The /2/status/ endpoint wraps the tweet under "status" (not "tweet"). Regression:
    fetch_tweet must extract it, or every live fetch returns "no usable data"."""
    m = load_tweet_module()
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    # Real /2/status/ shape: top-level "status", not "tweet".
    body = json.dumps({"code": 200, "message": "OK", "status": {
        "id": "1", "text": "via v2", "raw_text": {"text": "via v2"},
        "author": {"screen_name": "a", "name": "A"}, "media": {},
    }})

    class FakeResp:
        status = 200
        def __init__(self, body): self._b = body.encode("utf-8")
        def read(self): return self._b
        def getcode(self): return 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout=20: FakeResp(body))
    result = m.fetch_tweet("1")
    assert result.tweet is not None and result.tweet["text"] == "via v2"


def inventory_root() -> dict:
    return {
        "id": "100",
        "author": {"id": "author-1", "screen_name": "alice", "name": "Alice"},
        "text": "rendered root",
        "raw_text": {"text": "raw root", "facets": [{"url": "https://raw.test/link"}]},
        "article": {
            "id": "article-1",
            "title": "Root article",
            "content": {
                "blocks": [{"type": "unstyled", "text": "article body"}],
                "entityMap": {"0": {"type": "LINK", "data": {"url": "https://article.test/link"}}},
            },
        },
        "quote": {
            "id": "200",
            "author": {"id": "author-2", "screen_name": "bob", "name": "Bob"},
            "text": "quoted body",
            "article": {
                "title": "Quoted article",
                "content": {"blocks": [{"type": "unstyled", "text": "quoted article body"}]},
            },
        },
        "media": {"all": [{"id": "photo-1", "type": "photo", "url": "https://media.test/one.jpg"}]},
    }


def run_inventory(m, monkeypatch, capsys, body: bytes, endpoint: str = "status"):
    response = FakeResponse(body, response_url=f"https://api.fxtwitter.test/{endpoint}/100")
    calls = install_responses(monkeypatch, [response])
    code = m.main(["https://x.com/alice/status/100", "--inventory", endpoint])
    output = capsys.readouterr()
    return code, output, response, calls


def test_inventory_status_preserves_envelope_channels_paths_and_digest(monkeypatch, capsys):
    m = load_tweet_module()
    payload = {"code": 200, "message": "OK", "status": inventory_root(), "extra": {"keep": True}}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    monkeypatch.setattr(m, "fetch_tweet", lambda *_args: pytest.fail("inventory compacted a tweet"))
    monkeypatch.setattr(m, "fetch_thread", lambda *_args: pytest.fail("inventory followed a thread"))

    code, output, response, calls = run_inventory(m, monkeypatch, capsys, body)

    result = json.loads(output.out)
    assert code == 0
    assert output.err == ""
    assert calls == [("https://api.fxtwitter.com/2/status/100", 20)]
    assert response.read_sizes == [8 * 1024 * 1024 + 1]
    assert result["mode"] == "raw-response-inventory"
    assert result["endpoint"] == "status"
    assert result["extractor_schema"] == "kb-fxtwitter-extract/v2"
    assert result["raw_payload"] == payload
    assert result["raw_payload"]["extra"] == {"keep": True}
    assert result["root"]["items"][0]["payload_path"] == "$.status"
    assert result["root"]["items"][0]["raw_text"]["text"] == "raw root"
    assert result["root"]["items"][0]["text"] == "rendered root"
    assert result["articles"]["count"] == 2
    assert {item["parent_channel"] for item in result["articles"]["items"]} == {"root", "quote"}
    assert result["quotes"]["items"][0]["payload_path"] == "$.status.quote"
    assert result["direct_media"]["items"][0]["payload_path"] == "$.status.media.all[0]"
    assert result["raw_response"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["raw_response"]["byte_length"] == len(body)
    assert result["raw_response"]["raw_http_archive"] is False
    assert "not a raw HTTP archive" in result["raw_payload_note"]
    retrieved_at = result["transport"].pop("retrieved_at")
    assert datetime.datetime.fromisoformat(retrieved_at).utcoffset() == datetime.timedelta(0)
    assert result["transport"] == {
        "request_url": "https://api.fxtwitter.com/2/status/100",
        "response_url": "https://api.fxtwitter.test/status/100",
        "http_status": 200,
        "content_type": "application/json",
        "content_length": len(body),
        "attempts": 1,
    }


def test_inventory_thread_uses_one_selected_endpoint_and_keeps_other_author(monkeypatch, capsys):
    m = load_tweet_module()
    root = inventory_root()
    continuation = {
        "id": "101",
        "author": {"id": "author-1", "screen_name": "alice", "name": "Alice"},
        "text": "same author continuation",
    }
    other = {
        "id": "102",
        "author": {"id": "author-2", "screen_name": "bob", "name": "Bob"},
        "text": "other author continuation",
    }
    payload = {"code": 200, "status": root, "thread": [continuation, other]}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    monkeypatch.setattr(m, "fetch_tweet", lambda *_args: pytest.fail("inventory fetched a status helper"))
    monkeypatch.setattr(m, "fetch_thread", lambda *_args: pytest.fail("inventory fetched a thread helper"))

    code, output, _response, calls = run_inventory(m, monkeypatch, capsys, body, endpoint="thread")

    result = json.loads(output.out)
    assert code == 0
    assert calls == [("https://api.fxtwitter.com/2/thread/100", 20)]
    assert result["raw_payload"] == payload
    assert [item["id"] for item in result["sameauthor_continuations"]["items"]] == ["101"]
    assert [item["id"] for item in result["unknown_otherauthor"]["items"]] == ["102"]
    assert result["limits"]["attempts"] == 1


def test_inventory_transport_failure_is_single_attempt_without_backoff(monkeypatch, capsys):
    m = load_tweet_module()
    calls: list[str] = []

    def fail_once(request, timeout=20):
        calls.append(request.full_url)
        raise m.urllib.error.URLError("offline")

    monkeypatch.setattr(m, "_open_inventory_request", fail_once)
    code = m.main(["https://x.com/alice/status/100", "--inventory", "status"])
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert "request failed" in output.err
    assert calls == ["https://api.fxtwitter.com/2/status/100"]


def test_inventory_distinguishes_missing_and_empty_thread_channels(monkeypatch, capsys):
    m = load_tweet_module()
    missing = json.dumps({"status": inventory_root()}, separators=(",", ":")).encode("utf-8")
    empty_payload = {"status": inventory_root(), "thread": []}
    empty = json.dumps(empty_payload, separators=(",", ":")).encode("utf-8")
    responses = [FakeResponse(missing), FakeResponse(empty)]
    calls = install_responses(monkeypatch, responses)

    assert m.main(["https://x.com/alice/status/100", "--inventory", "status"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert m.main(["https://x.com/alice/status/100", "--inventory", "status"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert calls == [
        ("https://api.fxtwitter.com/2/status/100", 20),
        ("https://api.fxtwitter.com/2/status/100", 20),
    ]
    assert first["sameauthor_continuations"]["state"] == "missing"
    assert second["sameauthor_continuations"]["state"] == "empty"
    assert first["omissions"] != second["omissions"]


@pytest.mark.parametrize(
    "body, expected",
    [
        (b'{"status":{"id":"999","author":{"id":"a"}}}', "root id"),
        (b'{"status":{"id":"100","id":"101"}}', "DuplicateKeyError"),
        (b'{"status":{"id":"100","value":NaN}}', "non-standard JSON constant"),
        (
            b'{"status":{"id":"100"},"tweet":{"id":"100"}}',
            "duplicate status/tweet root envelopes",
        ),
    ],
)
def test_inventory_rejects_ambiguous_or_nonstandard_payloads(monkeypatch, capsys, body, expected):
    m = load_tweet_module()
    code, output, _response, calls = run_inventory(m, monkeypatch, capsys, body)

    assert code == 2
    assert output.out == ""
    assert expected in output.err
    assert len(calls) == 1


def test_inventory_rejects_oversize_response_before_decoding(monkeypatch, capsys):
    m = load_tweet_module()
    max_bytes = 8 * 1024 * 1024
    body = b"{" + b"x" * max_bytes
    code, output, response, calls = run_inventory(m, monkeypatch, capsys, body)

    assert code == 2
    assert output.out == ""
    assert "inventory limit" in output.err
    assert len(calls) == 1
    assert response.read_sizes == []


def test_out_is_rejected_before_transport_and_direct_sinks_are_guarded(monkeypatch, capsys, tmp_path):
    m = load_tweet_module()
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: pytest.fail("--out fetched"))
    output_path = tmp_path / "outside" / "artifact.md"

    with pytest.raises(SystemExit) as error:
        m.main(["https://x.com/alice/status/100", "--inventory", "status", "--out", str(output_path)])
    cli_output = capsys.readouterr()
    assert error.value.code == 2
    assert "--out is retired" in cli_output.err
    assert not output_path.exists()

    with pytest.raises(ValueError, match="--out is retired"):
        m.analyze(
            type(
                "Args",
                (),
                {"url": "https://x.com/alice/status/100", "out": "", "max_thread": 30, "max_quote_chain": 10},
            )()
        )
    direct_path = tmp_path / "direct.md"
    with pytest.raises(ValueError, match="--out is retired"):
        m.write_markdown_artifact({}, str(direct_path))
    assert not direct_path.exists()
    with pytest.raises(m.InventoryError, match="--out is retired"):
        m.inventory(type("Args", (), {"out": "", "inventory": "status"})())


@pytest.mark.parametrize("extra_byte, expected_code", [(False, 0), (True, 2)])
def test_inventory_enforces_byte_limit_without_content_length(monkeypatch, capsys, extra_byte, expected_code):
    m = load_tweet_module()
    extractor = m._load_inventory_extractor()
    monkeypatch.setattr(extractor, "DEFAULT_MAX_BYTES", 128)
    prefix = b'{"status":{"id":"100"}}'
    body = prefix + b" " * (128 - len(prefix)) + (b"x" if extra_byte else b"")
    response = FakeResponse(body)
    response.headers.pop("Content-Length")
    calls = install_responses(monkeypatch, [response])
    code = m.main(["https://x.com/alice/status/100", "--inventory", "status"])
    output = capsys.readouterr()
    assert code == expected_code
    assert len(calls) == 1
    assert response.read_sizes == [129]
    if extra_byte:
        assert output.out == ""
        assert "inventory limit" in output.err
    else:
        assert json.loads(output.out)["raw_response"]["byte_length"] == 128


@pytest.mark.parametrize("declared_offset", [-1, 1])
def test_inventory_rejects_content_length_mismatch(monkeypatch, capsys, declared_offset):
    m = load_tweet_module()
    body = b'{"status":{"id":"100"}}'
    response = FakeResponse(body)
    response.headers["Content-Length"] = str(len(body) + declared_offset)
    install_responses(monkeypatch, [response])
    assert m.main(["https://x.com/alice/status/100", "--inventory", "status"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Content-Length" in output.err


def test_inventory_does_not_guess_unavailable_response_url(monkeypatch, capsys):
    m = load_tweet_module()
    response = FakeResponse(b'{"status":{"id":"100"}}')
    install_responses(monkeypatch, [response])
    assert m.main(["https://x.com/alice/status/100", "--inventory", "status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["transport"]["response_url"] is None


def test_inventory_does_not_follow_redirects(monkeypatch, capsys):
    m = load_tweet_module()
    calls = []

    class RedirectResponseHandler(m.urllib.request.HTTPSHandler):
        def https_open(self, request):
            calls.append(request.full_url)
            response = m.urllib.response.addinfourl(
                io.BytesIO(b""), {"Location": "https://x.com/alice/status/100"},
                request.full_url, 302,
            )
            response.msg = "Found"
            return response

    monkeypatch.setattr(m.urllib.request, "HTTPSHandler", RedirectResponseHandler)
    assert m.main(["https://x.com/alice/status/100", "--inventory", "status"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "HTTP 302" in output.err
    assert calls == ["https://api.fxtwitter.com/2/status/100"]


@pytest.mark.parametrize("locator", [
    "https://unrelated.test/status/100", "https://user:password@x.com/alice/status/100",
    "https://x.com:1234/alice/status/100", "file:///status/100",
])
def test_inventory_rejects_unrelated_or_private_locator_before_transport(monkeypatch, locator):
    m = load_tweet_module()
    monkeypatch.setattr(m, "_open_inventory_request", lambda *_a, **_kw: pytest.fail("unexpected transport"))
    with pytest.raises(m.InventoryError, match="public X/Twitter/FxTwitter"):
        m.inventory_endpoint("100", "status", locator)


def test_inventory_records_locator_as_input_not_canonical_origin(monkeypatch, capsys):
    m = load_tweet_module()
    install_responses(monkeypatch, [FakeResponse(b'{"status":{"id":"100"}}')])
    locator = "https://fxtwitter.com/alice/status/100"
    assert m.main([locator, "--inventory", "status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["requested_locator"] == locator
    assert result["canonical_origin_established"] is False


def test_compact_output_is_explicitly_convenience_only(monkeypatch, capsys):
    m = load_tweet_module()
    main = fake_tweet(100, text="rendered", quote=None)
    monkeypatch.setattr(m, "fetch_tweet", lambda _tweet_id: m.TweetFetch(main))
    monkeypatch.setattr(m, "fetch_thread", lambda _tweet_id: ([main], None))

    assert m.main(["https://x.com/alice/status/100", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["output_kind"] == "compact-convenience"
    assert result["evidence_role"] == "convenience-only-not-source-evidence"
