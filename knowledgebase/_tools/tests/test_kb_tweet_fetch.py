from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-tweet-fetch.py"


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

    code = tweet_fetch.main(["https://x.com/alice/status/100", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["main"]["text"] == "main text"
    assert payload["main"]["photos"] == ["https://img.example/1.jpg", "https://img.example/2.jpg"]
    assert payload["quote"]["text"] == "quoted text"
    assert payload["video_urls"] == ["https://video.example/quoted.mp4"]


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
