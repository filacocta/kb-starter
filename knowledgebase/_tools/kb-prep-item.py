#!/usr/bin/env python3
"""Prepare a single KB ingestion item before fetching source text.

This helper centralizes the read-only pre-fetch steps used by interactive and
headless ingestion flows: dedup, transport mirror rewrite, and optional media
discovery. It writes nothing.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any


X_REWRITE_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
STATUS_ID_RE = re.compile(r"/(?:[^/]+/)*(?:status|statuses)/(\d+)(?:/|$)")


def load_kb_seen(kb_root: Path) -> Any:
    script = kb_root / "_tools" / "kb-seen.py"
    spec = importlib.util.spec_from_file_location("kb_seen", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mirror_fetch_url(url: str) -> tuple[str, bool]:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("nitter."):
        return url, False
    if host not in X_REWRITE_HOSTS:
        return url, False

    status = STATUS_ID_RE.search(parts.path)
    if status:
        return f"https://api.fxtwitter.com/2/thread/{status.group(1)}", True

    rewritten = urllib.parse.urlunsplit(
        ("https", "fxtwitter.com", parts.path or "/", parts.query, parts.fragment)
    )
    return rewritten, True


def discover_media(kb_root: Path, fetch_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    script = kb_root / "_tools" / "kb-media-capture.py"
    try:
        result = subprocess.run(
            [script, "--discover", fetch_url, "--filter", "chrome"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return [], [f"media discovery failed: {exc}"]

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            return [], [f"media discovery returned invalid JSON: {exc}: {detail}"]
        return [], [f"media discovery returned invalid JSON: {exc}"]

    if result.returncode != 0:
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            return [], [f"media discovery failed: {error}" for error in errors]
        return [], [f"media discovery exited {result.returncode}"]

    media = payload.get("media") if isinstance(payload, dict) else None
    return media if isinstance(media, list) else [], []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare one KB ingestion item.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    parser.add_argument("--skip-media", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()

    kb_seen = load_kb_seen(kb_root)
    canonical_url = kb_seen.normalize(args.url)
    seen_index = {norm: rel for norm, rel in kb_seen.iter_origins(kb_root)}
    seen = seen_index.get(canonical_url)
    fetch_url, mirror_applied = mirror_fetch_url(canonical_url)

    errors: list[str] = []
    media: list[dict[str, Any]] = []
    if not args.skip_media:
        media, media_errors = discover_media(kb_root, fetch_url)
        errors.extend(media_errors)

    print(
        json.dumps(
            {
                "url": canonical_url,
                "fetch_url": fetch_url,
                "mirror_applied": mirror_applied,
                "seen": seen,
                "media": media,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
