#!/usr/bin/env python3
"""source recovery helper: find a Wayback Machine snapshot for a (usually dead) URL.

Wraps the keyless Internet Archive availability API. Given a URL, returns the
closest archived snapshot (URL + capture timestamp + HTTP status). With --fetch,
also retrieves the snapshot's readable text so a source note can be populated
from it while keeping `origin:` set to the original dead URL.

Usage:
    kb-wayback.py "https://dead.example.com/article"
    kb-wayback.py "https://dead.example.com/article" --timestamp 20180101 --json
    kb-wayback.py "https://dead.example.com/article" --fetch   # prints snapshot text

Exit codes: 0 snapshot found; 3 no snapshot; 1 error.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

UA = "kb-bot/1.0 (+local knowledge-base; source recovery recovery)"


def available(url: str, timestamp: str | None):
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    if timestamp:
        api += f"&timestamp={timestamp}"
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    snap = data.get("archived_snapshots", {}).get("closest")
    return snap


def fetch_text(snapshot_url: str) -> str:
    # `id_` rawmode returns the archived page without the Wayback chrome/banner.
    raw = snapshot_url.replace("/http", "id_/http", 1) if "id_/" not in snapshot_url else snapshot_url
    req = urllib.request.Request(raw, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    # crude tag strip; the calling agent should clean further if needed
    import re
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--timestamp", help="preferred capture YYYYMMDD[hhmmss]")
    ap.add_argument("--fetch", action="store_true", help="also print snapshot text")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        snap = available(args.url, args.timestamp)
    except Exception as exc:
        print(f"error: wayback lookup failed: {exc}", file=sys.stderr)
        return 1

    if not snap or not snap.get("available"):
        print("no snapshot found", file=sys.stderr)
        return 3

    info = {
        "original": args.url,
        "snapshot_url": snap.get("url"),
        "timestamp": snap.get("timestamp"),
        "status": snap.get("status"),
    }
    if args.fetch:
        try:
            info["text"] = fetch_text(snap["url"])
        except Exception as exc:
            print(f"error: snapshot fetch failed: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"snapshot: {info['snapshot_url']}")
        print(f"captured: {info['timestamp']}  status: {info['status']}")
        if args.fetch:
            print("\n--- snapshot text ---\n")
            print(info["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
