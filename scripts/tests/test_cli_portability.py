"""Synthetic tests for the starter's portable CLI boundary.

These tests never consult a real agent config, provider, or personal vault.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def run_cli(name: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child_env = os.environ.copy()
    child_env.pop("KB_ROOT", None)
    child_env.pop("KB_CONFIG", None)
    child_env.pop("KB_CONFIG_PATH", None)
    child_env.pop("KB_AGENT", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=REPO,
        env=child_env,
        text=True,
        capture_output=True,
        check=False,
    )


def note(*, author: str = "kb-bot", note_type: str = "concept",
         topic: str = "demo", tags: str = "[]", body: str = "This is a stub.") -> str:
    return (
        "---\n"
        f"author: {author}\n"
        f"type: {note_type}\n"
        f"topic: {topic}\n"
        f"tags: {tags}\n"
        "---\n\n"
        f"{body}\n"
    )


def make_kb(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-kb"
    (root / "topics" / "demo" / "notes").mkdir(parents=True)
    (root / "projects").mkdir()
    return root


def test_resolver_requires_explicit_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import kb_config

    with pytest.raises(kb_config.KBConfigError, match="no KB root configured"):
        kb_config.resolve_kb_root(environ={"CODEX_HOME": str(tmp_path / "codex"),
                                        "CLAUDE_HOME": str(tmp_path / "claude")})


def test_config_env_paths_must_be_unambiguous(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    first = tmp_path / "one.conf"
    second = tmp_path / "two.conf"
    first.write_text(f"KB_ROOT: {root}\n", encoding="utf-8")
    second.write_text(f"KB_ROOT: {root}\n", encoding="utf-8")
    result = run_cli("kb-index", env={"KB_CONFIG": str(first), "KB_CONFIG_PATH": str(second)})
    assert result.returncode == 2
    assert "different files" in result.stderr
    assert not (root / "_kb-index.json").exists()


def test_index_and_search_keep_indexed_flow_with_explicit_root(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    (root / "topics" / "demo" / "notes" / "cellular.md").write_text(
        note(tags="[concept, demo]", body="Cellular automata are a concept."),
        encoding="utf-8",
    )
    indexed = run_cli("kb-index", "--kb-root", str(root))
    assert indexed.returncode == 0, indexed.stderr
    searched = run_cli("kb-search", "--kb-root", str(root), "--query", "cellular", "--json")
    assert searched.returncode == 0, searched.stderr
    payload = json.loads(searched.stdout)
    assert payload[0]["path"] == "topics/demo/notes/cellular.md"


def test_search_missing_index_is_read_only(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "unexpected-index-invocation"
    (fake_bin / "kb-index").write_text(
        f"#!/bin/sh\ntouch '{marker}'\nexit 91\n", encoding="utf-8"
    )
    (fake_bin / "kb-index").chmod(0o755)
    result = run_cli(
        "kb-search", "--kb-root", str(root),
        env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
    )
    assert result.returncode == 2
    assert "run kb-index explicitly" in result.stderr
    assert not marker.exists()
    assert not (root / "_kb-index.json").exists()


def test_tag_apply_only_changes_unambiguous_bot_notes(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    owned = root / "topics" / "demo" / "notes" / "owned.md"
    human = root / "topics" / "demo" / "notes" / "human.md"
    source = root / "topics" / "demo" / "notes" / "source.md"
    owned.write_text(note(), encoding="utf-8")
    human.write_text(note(author="human-user"), encoding="utf-8")
    source.write_text(note(note_type="source"), encoding="utf-8")
    original_human = human.read_text(encoding="utf-8")
    original_source = source.read_text(encoding="utf-8")

    result = run_cli("kb-tag", "--kb-root", str(root), "--apply")
    assert result.returncode == 1, result.stderr
    assert "tags: [concept, demo]" in owned.read_text(encoding="utf-8")
    assert human.read_text(encoding="utf-8") == original_human
    assert source.read_text(encoding="utf-8") == original_source


def test_tag_content_is_a_read_only_handoff_and_skips_symlinks(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    owned = root / "topics" / "demo" / "notes" / "owned.md"
    human = root / "topics" / "demo" / "notes" / "human.md"
    owned.write_text(note(tags="[concept, demo]"), encoding="utf-8")
    human.write_text(note(author="human"), encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text(note(tags="[]"), encoding="utf-8")
    (root / "topics" / "demo" / "notes" / "linked.md").symlink_to(outside)
    original_owned = owned.read_text(encoding="utf-8")

    result = run_cli("kb-tag-content", "--kb-root", str(root), "--topic", "demo", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["read_only"] is True
    assert payload["active_harness"] == "your active harness"
    assert [item["path"] for item in payload["candidates"]] == [
        "topics/demo/notes/owned.md"
    ]
    assert owned.read_text(encoding="utf-8") == original_owned
    assert outside.read_text(encoding="utf-8") == note(tags="[]")


def test_topic_arguments_reject_path_traversal(tmp_path: Path) -> None:
    root = make_kb(tmp_path)
    result = run_cli("kb-lint", "--kb-root", str(root), "--topic", "../demo")
    assert result.returncode == 3
    assert "single path-safe slug" in result.stderr
