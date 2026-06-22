#!/usr/bin/env python3
"""Ingestion-level dedup for the KB.

Truth source: the `origin:` and `mirror:` frontmatter of successful source notes
(`topics/*/sources/*.md`). Source notes marked `ingest_status: failed` document
attempted fetches, but they are not treated as ingested. Reading-list `[x]` is
NOT authoritative (it can mean assigned/dropped/routed). A generated JSON cache
(`_seen-urls.json`) speeds lookups; it is only a cache and is rebuilt from the
source notes.

Usage:
    kb-seen.py --build                  # (re)build _seen-urls.json from source notes
    kb-seen.py --check <url> [<url> ...] # report whether each URL is already ingested
    kb-seen.py --normalize <url>        # print the canonical form (debug)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "s", "t", "login", "igshid", "spm",
}
X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "fxtwitter.com",
           "vxtwitter.com", "fixupx.com", "api.fxtwitter.com", "nitter.net"}
SEEN_FILE = "_seen-urls.json"


def normalize(url: str) -> str:
    """Return a canonical form for dedup comparison. Conservative: keep
    meaningful query params, treat PDFs and landing pages as distinct."""
    url = url.strip().strip(".,);]")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url.lower()
    scheme = "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or ""

    # X / Twitter family -> canonical x.com/i/status/<id>, keyed on the status
    # ID only. The username/path prefix varies across mirrors (handle, "i",
    # "2" for the api host), but the status ID is the stable identifier, so all
    # forms of the same tweet must collapse to one key.
    if host in X_HOSTS:
        m = re.search(r"/(?:status|statuses|thread)/(\d+)", path)
        if m:
            return f"https://x.com/i/status/{m.group(1)}"
        host = "x.com"  # non-status X URL (profile etc.): just canonicalize host

    # arXiv: pdf form -> abs form
    if host == "arxiv.org":
        m = re.match(r"/pdf/(.+?)(?:\.pdf)?$", path)
        if m:
            path = f"/abs/{m.group(1)}"

    # Strip tracking-only query keys; keep the rest (sorted for stability).
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in TRACKING_KEYS]
    query = urllib.parse.urlencode(sorted(q))

    # Normalize trailing slash (but never strip a file extension).
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    if not path:
        path = "/"

    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "-")):
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def iter_origins(kb_root: Path):
    """Yield (normalized_url, source_note_relpath) for every URL found in the
    origin:/mirror: frontmatter of successful source notes. Handles messy
    values: multiple URLs in one scalar, local paths (skipped), etc."""
    for note in sorted(kb_root.glob("topics/*/sources/*.md")):
        fm = _frontmatter(note.read_text(encoding="utf-8"))
        if fm.get("type") != "source":
            continue
        if fm.get("ingest_status", "").lower() == "failed":
            continue
        rel = note.relative_to(kb_root).as_posix()
        for field in ("origin", "mirror"):
            raw = fm.get(field, "")
            for url in URL_RE.findall(raw):
                yield normalize(url), rel


def build_index(kb_root: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    for norm, rel in iter_origins(kb_root):
        index.setdefault(norm, rel)
    (kb_root / SEEN_FILE).write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def load_index(kb_root: Path) -> dict[str, str]:
    path = kb_root / SEEN_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return build_index(kb_root)


def is_ingested(url: str, kb_root: Path, index: dict[str, str] | None = None) -> str | None:
    """Return the source-note path if `url` is already ingested, else None."""
    if index is None:
        index = load_index(kb_root)
    return index.get(normalize(url))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KB ingestion-level dedup helper.")
    p.add_argument("--kb-root", default=str(Path.home() / "Vault" / "knowledgebase"))
    p.add_argument("--build", action="store_true", help="rebuild the _seen-urls.json cache")
    p.add_argument("--check", nargs="+", metavar="URL", help="report whether URL(s) are ingested")
    p.add_argument("--normalize", nargs="+", metavar="URL", help="print canonical form (debug)")
    args = p.parse_args(argv)
    kb_root = Path(args.kb_root).expanduser().resolve()

    if args.normalize:
        for u in args.normalize:
            print(f"{u}\n  -> {normalize(u)}")
        return 0

    if args.build:
        idx = build_index(kb_root)
        print(json.dumps({"built": SEEN_FILE, "unique_urls": len(idx)}, indent=2))
        return 0

    if args.check:
        idx = load_index(kb_root)
        out = {u: is_ingested(u, kb_root, idx) for u in args.check}
        print(json.dumps({"ingested": out}, indent=2))
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
