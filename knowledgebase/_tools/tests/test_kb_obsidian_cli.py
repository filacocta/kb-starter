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


def write_fake_obsidian(bin_dir: Path, body: str, reported_vault: Path | None = None) -> Path:
    binary = bin_dir / "obsidian"
    probe = (
        'if [ "$1" = "vault" ] && [ "$2" = "info=path" ]; then\n'
        + (f"  echo '{reported_vault}'\n" if reported_vault else
           '  if [ -d ../.obsidian ]; then (cd .. && pwd); else pwd; fi\n')
        + '  exit 0\nfi\n'
    )
    binary.write_text("#!/usr/bin/env bash\n" + probe + body, encoding="utf-8")
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


def test_uses_flatpak_runtime_socket_from_xdg_base(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    runtime = tmp_path / "run"
    flatpak_runtime = runtime / ".flatpak" / cli.OBSIDIAN_FLATPAK_ID / "xdg-run"
    flatpak_runtime.mkdir(parents=True)
    (flatpak_runtime / ".obsidian-cli.sock").touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_obsidian(bin_dir, 'printf "%s\\n" "$XDG_RUNTIME_DIR"\n')
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    result = cli.run_obsidian(kb_root, ["version"])

    assert result["ok"] is True
    assert result["stdout"] == str(flatpak_runtime)
    assert result["runtime_source"] == "flatpak-xdg-run"
    assert result["socket"] == str(flatpak_runtime / ".obsidian-cli.sock")


def test_graph_health_post_filters_topic_relative_to_vault_root(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    vault = tmp_path / "Vault"
    kb_root = vault / "knowledgebase"
    kb_root.mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "args.log"
    write_fake_obsidian(
        bin_dir,
        f"printf '%s\\n' \"$@\" >> {log}\n"
        f"printf '%s\\n' '---' >> {log}\n"
        "case \"$1\" in\n"
        "  version) echo '1.12.7' ;;\n"
        f"  vault) echo '{vault}' ;;\n"
        "  unresolved) printf '%s\\n' '[{\"link\":\"topic-missing\",\"count\":\"2\",\"sources\":\"knowledgebase/topics/earthquake-prediction/a.md, projects/outside.md\"},{\"link\":\"other-missing\",\"count\":\"1\",\"sources\":\"knowledgebase/topics/ai-literacy/b.md\"}]' ;;\n"
        "  orphans) printf '%s\\n' 'knowledgebase/topics/earthquake-prediction/orphan.md' 'knowledgebase/topics/ai-literacy/orphan.md' ;;\n"
        "  deadends) printf '%s\\n' 'knowledgebase/topics/earthquake-prediction/deadend.md' 'projects/outside.md' ;;\n"
        "  *) echo '[]' ;;\n"
        "esac\n",
    )
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    payload = cli.graph_health(kb_root, "earthquake-prediction")
    unresolved = json.loads(payload["unresolved"]["stdout"])

    assert payload["scope_path"] == "knowledgebase/topics/earthquake-prediction"
    assert payload["unresolved"]["ok"] is True
    assert [row["link"] for row in unresolved] == ["topic-missing"]
    assert unresolved[0]["sources"] == "knowledgebase/topics/earthquake-prediction/a.md"
    assert payload["orphans_total"]["stdout"] == "1"
    assert payload["orphans_total"]["paths"] == ["knowledgebase/topics/earthquake-prediction/orphan.md"]
    assert payload["deadends_total"]["stdout"] == "1"
    assert payload["deadends_total"]["paths"] == ["knowledgebase/topics/earthquake-prediction/deadend.md"]
    assert "path=knowledgebase/topics/earthquake-prediction" not in log.read_text(encoding="utf-8").splitlines()


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


def test_search_refuses_other_vault_before_content_access(tmp_path, monkeypatch) -> None:
    cli = load_obsidian_module()
    root = tmp_path / "personal"
    root.mkdir()
    other = tmp_path / "work"
    other.mkdir()
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    marker = tmp_path / "content-was-read"
    write_fake_obsidian(binary_dir, f"touch '{marker}'\n", reported_vault=other)
    monkeypatch.setenv("PATH", os.pathsep.join([str(binary_dir), os.environ.get("PATH", "")]))

    result = cli.search(root, "private fact", None, 5)["result"]

    assert result["ok"] is False
    assert "vault" in result["stderr"] and "match" in result["stderr"]
    assert not marker.exists()


def test_graph_command_failure_has_nonzero_exit(tmp_path, monkeypatch, capsys) -> None:
    cli = load_obsidian_module()
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr(cli, "graph_health", lambda root, topic: {"unresolved": {"ok": False}})
    assert cli.main(["--kb-root", str(root), "graph-health"]) == 2
    assert json.loads(capsys.readouterr().out)["unresolved"]["ok"] is False
