#!/usr/bin/env python3
"""Optional read-only bridge to Obsidian CLI for KB diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 20
OBSIDIAN_FLATPAK_ID = "md.obsidian.Obsidian"


def valid_vault_path(value: str | None) -> bool:
    if value is None:
        return True
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def cli_runtime_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    bases = [Path(xdg_runtime)] if xdg_runtime else []
    default_runtime = Path(f"/run/user/{os.getuid()}")
    if default_runtime not in bases:
        bases.append(default_runtime)

    for base in bases:
        candidates.append(("xdg-runtime", base))
        candidates.append(("flatpak-xdg-run", base / ".flatpak" / OBSIDIAN_FLATPAK_ID / "xdg-run"))

    candidates.append(("home", Path.home()))
    return candidates


def obsidian_cli_env() -> tuple[dict[str, str] | None, dict[str, Any]]:
    checked: list[str] = []
    for source, runtime_dir in cli_runtime_candidates():
        socket_path = runtime_dir / ".obsidian-cli.sock"
        checked.append(str(socket_path))
        if socket_path.exists():
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(runtime_dir)
            return env, {
                "runtime_source": source,
                "runtime_dir": str(runtime_dir),
                "socket": str(socket_path),
                "socket_exists": True,
                "checked_sockets": checked,
            }

    return None, {
        "runtime_source": None,
        "runtime_dir": os.environ.get("XDG_RUNTIME_DIR"),
        "socket": None,
        "socket_exists": False,
        "checked_sockets": checked,
    }


def kb_path_prefix(kb_root: Path) -> str:
    if (kb_root / ".obsidian").is_dir():
        return ""
    if (kb_root.parent / ".obsidian").is_dir():
        return kb_root.name
    return ""


def to_vault_path(kb_root: Path, kb_relative_path: str) -> str:
    prefix = kb_path_prefix(kb_root)
    return f"{prefix}/{kb_relative_path}" if prefix else kb_relative_path


def path_in_scope(path: str, scope_path: str) -> bool:
    normalized = path.strip()
    scope = scope_path.rstrip("/")
    return normalized == scope or normalized.startswith(f"{scope}/")


def split_source_paths(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def filter_unresolved_by_scope(result: dict[str, Any], scope_path: str) -> dict[str, Any]:
    if not result.get("ok"):
        return result
    try:
        rows = json.loads(result.get("stdout") or "[]")
    except json.JSONDecodeError as exc:
        filtered = dict(result)
        filtered["post_filter_error"] = f"could not parse unresolved JSON: {exc}"
        return filtered

    scoped_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sources = split_source_paths(row.get("sources"))
        matching_sources = [source for source in sources if path_in_scope(source, scope_path)]
        if not matching_sources:
            continue
        scoped = dict(row)
        scoped["sources"] = ", ".join(matching_sources)
        scoped["matched_source_count"] = len(matching_sources)
        scoped_rows.append(scoped)

    filtered = dict(result)
    filtered["stdout"] = json.dumps(scoped_rows, indent=2)
    filtered["post_filter"] = {
        "path": scope_path,
        "mode": "sources",
        "input_count": len(rows) if isinstance(rows, list) else None,
        "output_count": len(scoped_rows),
    }
    return filtered


def filter_line_paths_by_scope(result: dict[str, Any], scope_path: str) -> dict[str, Any]:
    if not result.get("ok"):
        return result
    paths = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip()]
    scoped_paths = [path for path in paths if path_in_scope(path, scope_path)]
    filtered = dict(result)
    filtered["stdout"] = str(len(scoped_paths))
    filtered["paths"] = scoped_paths
    filtered["post_filter"] = {
        "path": scope_path,
        "mode": "paths",
        "input_count": len(paths),
        "output_count": len(scoped_paths),
    }
    return filtered


def skipped_vault_wide_result(command: list[str], scope_path: str) -> dict[str, Any]:
    return {
        "ok": None,
        "available": True,
        "command": ["obsidian", *command],
        "stdout": "",
        "stderr": "",
        "returncode": None,
        "skipped": True,
        "reason": f"command is vault-wide and was skipped for topic scope {scope_path}",
    }


def connection_hint(result: dict[str, Any]) -> str | None:
    if result.get("ok") or not result.get("socket_exists"):
        return None
    error = f"{result.get('stderr', '')}\n{result.get('stdout', '')}"
    if "unable to find Obsidian" not in error:
        return None
    return (
        "Obsidian CLI socket exists, but the command could not connect. "
        "If this is running under Codex or another sandbox, rerun outside the sandbox "
        "or with host/desktop IPC access."
    )


def run_obsidian(kb_root: Path, args: list[str], timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    binary = shutil.which("obsidian")
    env, cli_context = obsidian_cli_env()
    if not binary:
        return {
            "ok": False,
            "available": False,
            "command": ["obsidian", *args],
            "stdout": "",
            "stderr": "obsidian binary not found on PATH",
            "returncode": 127,
            **cli_context,
        }

    command = [binary, *args]
    if args and args[0] not in {"version", "vault", "vaults", "help"}:
        selected = run_obsidian(kb_root, ["vault", "info=path"], timeout)
        expected = kb_root.parent if kb_path_prefix(kb_root) else kb_root
        if not selected.get("ok") or selected.get("stdout") != str(expected):
            return {
                "ok": False, "available": True, "command": command,
                "stdout": "", "stderr": "selected Obsidian vault does not match the requested KB root",
                "returncode": 4, "expected_vault": str(expected),
            }
    try:
        proc = subprocess.run(
            command,
            cwd=kb_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "available": True,
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": f"timed out after {timeout}s",
            "returncode": None,
            **cli_context,
        }

    result = {
        "ok": proc.returncode == 0,
        "available": True,
        "command": command,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
        **cli_context,
    }
    hint = connection_hint(result)
    if hint:
        result["hint"] = hint
    return result


def status(kb_root: Path) -> dict[str, Any]:
    binary = shutil.which("obsidian")
    payload: dict[str, Any] = {
        "kb_root": str(kb_root),
        "binary": binary,
        "available": bool(binary),
        "app_connected": False,
    }
    if not binary:
        payload["error"] = "obsidian binary not found on PATH"
        return payload

    version = run_obsidian(kb_root, ["version"])
    payload["version"] = version
    payload["app_connected"] = version["ok"]
    if version["ok"]:
        payload["vault"] = run_obsidian(kb_root, ["vault", "info=path"])
        expected = kb_root.parent if kb_path_prefix(kb_root) else kb_root
        payload["vault_matches_root"] = (
            payload["vault"].get("ok") and payload["vault"].get("stdout") == str(expected)
        )
        if not payload["vault_matches_root"]:
            payload["error"] = "selected Obsidian vault does not match the requested KB root"
    else:
        payload["error"] = version["stderr"] or version["stdout"]
    return payload


def graph_health(kb_root: Path, topic: str | None) -> dict[str, Any]:
    if topic and not valid_vault_path(f"topics/{topic}"):
        return {"error": f"invalid topic path: {topic}", "scope": topic}
    scope_path = to_vault_path(kb_root, f"topics/{topic}") if topic else None
    unresolved = run_obsidian(kb_root, ["unresolved", "verbose", "format=json"])
    orphans = run_obsidian(kb_root, ["orphans"] if topic else ["orphans", "total"])
    deadends = run_obsidian(kb_root, ["deadends"] if topic else ["deadends", "total"])
    if scope_path:
        unresolved = filter_unresolved_by_scope(unresolved, scope_path)
        orphans = filter_line_paths_by_scope(orphans, scope_path)
        deadends = filter_line_paths_by_scope(deadends, scope_path)
        tags = skipped_vault_wide_result(["tags", "counts", "format=json"], scope_path)
        properties = skipped_vault_wide_result(["properties", "counts", "format=json"], scope_path)
    else:
        tags = run_obsidian(kb_root, ["tags", "counts", "format=json"])
        properties = run_obsidian(kb_root, ["properties", "counts", "format=json"])

    return {
        "kb_root": str(kb_root),
        "scope": topic or "all",
        "scope_path": scope_path,
        "scope_note": (
            "Obsidian CLI graph commands are vault-wide; topic scope is post-filtered by source/file path."
            if topic
            else None
        ),
        "status": status(kb_root),
        "unresolved": unresolved,
        "orphans_total": orphans,
        "deadends_total": deadends,
        "tags": tags,
        "properties": properties,
    }


def search(kb_root: Path, query: str, path: str | None, limit: int) -> dict[str, Any]:
    if not valid_vault_path(path):
        return {"error": f"invalid vault path: {path}", "query": query, "path": path}
    args = ["search:context", f"query={query}", f"limit={limit}"]
    if path:
        args.append(f"path={to_vault_path(kb_root, path)}")
    return {"query": query, "path": path, "result": run_obsidian(kb_root, args)}


def tasks(kb_root: Path, path: str | None, done: bool) -> dict[str, Any]:
    if not valid_vault_path(path):
        return {"error": f"invalid vault path: {path}", "path": path, "done": done}
    args = ["tasks", "verbose", "format=json"]
    args.append("done" if done else "todo")
    if path:
        args.append(f"path={to_vault_path(kb_root, path)}")
    return {"path": path, "done": done, "result": run_obsidian(kb_root, args)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Obsidian CLI diagnostics for the KB.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    graph = sub.add_parser("graph-health")
    graph.add_argument("--topic")

    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--path")
    search_parser.add_argument("--limit", type=int, default=20)

    tasks_parser = sub.add_parser("tasks")
    tasks_parser.add_argument("--path")
    tasks_parser.add_argument("--done", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    if not kb_root.exists():
        print(json.dumps({"error": f"KB root not found: {kb_root}"}, indent=2))
        return 2

    if args.command == "status":
        payload = status(kb_root)
    elif args.command == "graph-health":
        payload = graph_health(kb_root, args.topic)
    elif args.command == "search":
        payload = search(kb_root, args.query, args.path, args.limit)
    elif args.command == "tasks":
        payload = tasks(kb_root, args.path, args.done)
    else:
        raise AssertionError(args.command)

    print(json.dumps(payload, indent=2))
    failed = payload.get("error") or payload.get("result", {}).get("ok") is False
    failed = failed or payload.get("status", {}).get("error")
    failed = failed or any(isinstance(value, dict) and value.get("ok") is False
                           and not value.get("skipped") for value in payload.values())
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
