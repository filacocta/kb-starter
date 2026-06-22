from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-obsidian-cli.py"


def load_obsidian_module():
    spec = importlib.util.spec_from_file_location("kb_obsidian_cli", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_fake_obsidian(bin_dir: Path, body: str) -> Path:
    binary = bin_dir / "obsidian"
    binary.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_status_reports_unavailable_when_binary_missing(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    payload = cli.status(kb_root)

    assert payload["available"] is False
    assert payload["app_connected"] is False
    assert "not found" in payload["error"]


def test_status_reports_app_connection_failure(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_obsidian(bin_dir, 'echo "Obsidian is not running" >&2\nexit 1\n')
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    payload = cli.status(kb_root)

    assert payload["available"] is True
    assert payload["app_connected"] is False
    assert payload["version"]["returncode"] == 1
    assert "not running" in payload["error"]


def test_search_passes_context_command_and_path(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "args.json"
    write_fake_obsidian(
        bin_dir,
        f"printf '%s\\n' \"$@\" > {log}\n"
        "echo 'topics/foo/_summary.md:3: matched text'\n",
    )
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    payload = cli.search(kb_root, "matched text", "topics/foo", 5)

    assert payload["result"]["ok"] is True
    assert payload["result"]["stdout"] == "topics/foo/_summary.md:3: matched text"
    assert log.read_text(encoding="utf-8").splitlines() == [
        "search:context",
        "query=matched text",
        "limit=5",
        "path=topics/foo",
    ]


def test_rejects_unsafe_vault_paths(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "called"
    write_fake_obsidian(bin_dir, f"touch {marker}\n")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    assert "invalid vault path" in cli.search(kb_root, "x", "../outside", 5)["error"]
    assert "invalid vault path" in cli.tasks(kb_root, "/tmp/outside", False)["error"]
    assert "invalid topic path" in cli.graph_health(kb_root, "../outside")["error"]
    assert not marker.exists()


def test_main_outputs_json(tmp_path, monkeypatch, capsys) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_obsidian(bin_dir, 'echo "1.12.7"\n')
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    assert cli.main(["--kb-root", str(kb_root), "status"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["app_connected"] is True
    assert payload["version"]["stdout"] == "1.12.7"
