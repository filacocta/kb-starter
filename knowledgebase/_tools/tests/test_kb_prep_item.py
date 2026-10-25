from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-prep-item.py"


def load_prep_module():
    spec = importlib.util.spec_from_file_location("kb_prep_item", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path) -> Path:
    kb_root = tmp_path / "kb"
    tools = kb_root / "_tools"
    sources = kb_root / "topics" / "test-topic" / "sources"
    tools.mkdir(parents=True)
    sources.mkdir(parents=True)
    shutil.copy(ROOT / "_tools" / "kb-seen.py", tools / "kb-seen.py")
    shutil.copy(ROOT / "_tools" / "kb-write-guard.py", tools / "kb-write-guard.py")
    return kb_root


def test_mirror_rewrite_table() -> None:
    prep = load_prep_module()

    assert prep.mirror_fetch_url("https://x.com/alice/status/12345") == (
        "https://api.fxtwitter.com/2/thread/12345",
        True,
    )
    assert prep.mirror_fetch_url("https://twitter.com/alice") == (
        "https://fxtwitter.com/alice",
        True,
    )
    assert prep.mirror_fetch_url("https://example.com/alice/status/12345") == (
        "https://example.com/alice/status/12345",
        False,
    )
    assert prep.mirror_fetch_url("https://nitter.net/alice/status/12345") == (
        "https://nitter.net/alice/status/12345",
        False,
    )


def test_main_reports_seen_and_unseen_items(tmp_path, capsys) -> None:
    prep = load_prep_module()
    kb_root = make_kb(tmp_path)
    (kb_root / "topics" / "test-topic" / "sources" / "seen.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "type: source\n"
        "origin: https://example.com/article?utm_source=newsletter\n"
        "---\n\n"
        "# Seen\n",
        encoding="utf-8",
    )

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://example.com/article", "--skip-media"]) == 0
    seen_payload = json.loads(capsys.readouterr().out)
    assert seen_payload["url"] == "https://example.com/article"
    assert seen_payload["seen"] == "topics/test-topic/sources/seen.md"
    assert seen_payload["media"] == []
    assert seen_payload["errors"] == []
    assert not (kb_root / "_seen-urls.json").exists()

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://example.com/other", "--skip-media"]) == 0
    unseen_payload = json.loads(capsys.readouterr().out)
    assert unseen_payload["seen"] is None


def test_main_skips_media_discovery_for_seen_item(tmp_path, monkeypatch, capsys) -> None:
    prep = load_prep_module()
    kb_root = make_kb(tmp_path)
    (kb_root / "topics" / "test-topic" / "sources" / "seen.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "type: source\n"
        "origin: https://example.com/article\n"
        "---\n\n"
        "# Seen\n",
        encoding="utf-8",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("duplicate item must not trigger media discovery")

    monkeypatch.setattr(prep, "discover_media", fail_if_called)

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://example.com/article"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["seen"] == "topics/test-topic/sources/seen.md"
    assert payload["media"] == []
    assert payload["errors"] == []


def test_main_skips_media_for_governed_alias_without_network(tmp_path, monkeypatch, capsys) -> None:
    prep = load_prep_module()
    kb_root = make_kb(tmp_path)
    source = kb_root / "topics" / "test-topic" / "sources" / "seen.md"
    source.write_text(
        "---\n"
        "author: kb-bot\n"
        "type: source\n"
        "origin: https://example.com/current\n"
        "ingest_status: succeeded\n"
        "---\n\n"
        "# Seen\n",
        encoding="utf-8",
    )
    (kb_root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": "https://example.com/legacy",
                        "source_note": "topics/test-topic/sources/seen.md",
                        "evidence": "manual redirect audit",
                        "verified": "2026-09-06",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("a governed alias hit must not trigger media discovery")

    monkeypatch.setattr(prep, "discover_media", fail_if_called)

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://example.com/legacy"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seen"] == "topics/test-topic/sources/seen.md"
    assert payload["media"] == []
    assert payload["errors"] == []


def test_main_discovers_media_with_mirror_rewrite(tmp_path, monkeypatch, capsys) -> None:
    prep = load_prep_module()
    kb_root = make_kb(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"media": [{"kind": "image", "origin_url": "https://cdn.example/img.jpg"}]}),
            "",
        )

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://www.twitter.com/alice/status/12345"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["url"] == "https://x.com/i/status/12345"
    assert payload["fetch_url"] == "https://api.fxtwitter.com/2/thread/12345"
    assert payload["mirror_applied"] is True
    assert payload["media"] == [{"kind": "image", "origin_url": "https://cdn.example/img.jpg"}]
    assert payload["errors"] == []
    assert calls == [
        (
            [
                kb_root / "_tools" / "kb-media-capture.py",
                "--discover",
                "https://x.com/i/status/12345",
                "--filter",
                "chrome",
            ],
            {"capture_output": True, "text": True, "check": False, "timeout": 30},
        )
    ]


def test_media_failure_is_non_fatal(tmp_path, monkeypatch, capsys) -> None:
    prep = load_prep_module()
    kb_root = make_kb(tmp_path)

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, json.dumps({"media": [], "errors": ["blocked"]}), "")

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    assert prep.main(["--kb-root", str(kb_root), "--url", "https://example.com/article"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["media"] == []
    assert payload["errors"] == ["media discovery failed: blocked"]
