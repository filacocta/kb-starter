#!/usr/bin/env python3
"""Clean unchecked global inbox rows that are already ingested.

This is a repair wrapper around `kb-lint.py` + `kb-checkoff.py`: lint detects
stale `readinglist/Links.md` rows, and apply mode marks each one through the
shared lock-safe checkoff tool.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_lint_module(tools_dir: Path) -> Any:
    module_path = tools_dir / "kb-lint.py"
    spec = importlib.util.spec_from_file_location("kb_lint", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stale_findings(kb_root: Path, tools_dir: Path) -> tuple[Any, list[dict[str, Any]]]:
    lint = load_lint_module(tools_dir)
    findings: list[dict[str, Any]] = []
    lint.check_reading_lists(kb_root, None, findings)
    stale = [finding for finding in findings if finding.get("code") == "global-inbox-already-ingested"]
    return lint, stale


def checkoff_command(kb_root: Path, tools_dir: Path, url: str, source_note_link: str) -> list[str]:
    return [
        sys.executable,
        str(tools_dir / "kb-checkoff.py"),
        "--kb-root",
        str(kb_root),
        "--file",
        "readinglist/Links.md",
        "--url",
        url,
        "--mark",
        "x",
        "--note",
        f"already ingested -> {source_note_link}",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mark stale global inbox rows using authoritative source/alias lookup.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true", help="mark stale rows via kb-checkoff.py; default is dry-run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()
    tools_dir = Path(__file__).resolve().parent
    if not kb_root.exists():
        print(json.dumps({"error": f"KB root not found: {kb_root}"}))
        return 2

    lint, findings = stale_findings(kb_root, tools_dir)
    actions: list[dict[str, Any]] = []
    failed = False
    for finding in findings:
        url = str(finding["url"])
        source_note = str(finding["source_note"])
        source_link = lint.source_note_link(source_note)
        command = checkoff_command(kb_root, tools_dir, url, source_link)
        action: dict[str, Any] = {
            "line": finding.get("line"),
            "url": url,
            "source_note": source_note,
            "command": " ".join(shlex.quote(part) for part in command),
        }
        if args.apply:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
            action["returncode"] = proc.returncode
            action["stdout"] = proc.stdout.strip()
            action["stderr"] = proc.stderr.strip()
            if proc.returncode != 0:
                failed = True
        actions.append(action)

    result = {
        "kb_root": str(kb_root),
        "apply": bool(args.apply),
        "stale_count": len(findings),
        "actions": actions,
    }
    print(json.dumps(result, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
