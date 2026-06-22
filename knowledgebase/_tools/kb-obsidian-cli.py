#!/usr/bin/env python3
"""Optional read-only bridge to Obsidian CLI for KB diagnostics."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 20


def valid_vault_path(value: str | None) -> bool:
    if value is None:
        return True
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def run_obsidian(kb_root: Path, args: list[str], timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    binary = shutil.which("obsidian")
    if not binary:
        return {
            "ok": False,
            "available": False,
            "command": ["obsidian", *args],
            "stdout": "",
            "stderr": "obsidian binary not found on PATH",
            "returncode": 127,
        }

    command = [binary, *args]
    try:
        proc = subprocess.run(
            command,
            cwd=kb_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "available": True,
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": f"timed out after {timeout}s",
            "returncode": None,
        }

    return {
        "ok": proc.returncode == 0,
        "available": True,
        "command": command,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
    }


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
    else:
        payload["error"] = version["stderr"] or version["stdout"]
    return payload


def graph_health(kb_root: Path, topic: str | None) -> dict[str, Any]:
    if topic and not valid_vault_path(f"topics/{topic}"):
        return {"error": f"invalid topic path: {topic}", "scope": topic}
    path_arg = [f"path=topics/{topic}"] if topic else []
    return {
        "kb_root": str(kb_root),
        "scope": topic or "all",
        "status": status(kb_root),
        "unresolved": run_obsidian(kb_root, ["unresolved", "verbose", "format=json", *path_arg]),
        "orphans_total": run_obsidian(kb_root, ["orphans", "total"]),
        "deadends_total": run_obsidian(kb_root, ["deadends", "total"]),
        "tags": run_obsidian(kb_root, ["tags", "counts", "format=json"]),
        "properties": run_obsidian(kb_root, ["properties", "counts", "format=json"]),
    }


def search(kb_root: Path, query: str, path: str | None, limit: int) -> dict[str, Any]:
    if not valid_vault_path(path):
        return {"error": f"invalid vault path: {path}", "query": query, "path": path}
    args = ["search:context", f"query={query}", f"limit={limit}"]
    if path:
        args.append(f"path={path}")
    return {"query": query, "path": path, "result": run_obsidian(kb_root, args)}


def tasks(kb_root: Path, path: str | None, done: bool) -> dict[str, Any]:
    if not valid_vault_path(path):
        return {"error": f"invalid vault path: {path}", "path": path, "done": done}
    args = ["tasks", "verbose", "format=json"]
    args.append("done" if done else "todo")
    if path:
        args.append(f"path={path}")
    return {"path": path, "done": done, "result": run_obsidian(kb_root, args)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Obsidian CLI diagnostics for the KB.")
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
