#!/usr/bin/env python3
"""reference discovery helper: resolve a free-text scholarly citation to canonical URLs.

Uses the keyless Crossref REST API to turn a bibliographic string (the kind an
agent extracts from a tweet's prose or from OCR of a journal screenshot) into
ranked candidate works with DOIs, then queries the keyless Unpaywall-style
OpenAlex `best_oa_location` to surface a freely-readable copy when one exists.
Per reference discovery, the agent QUEUES the most freely-readable canonical candidate and
keeps the DOI/publisher URL as the citation anchor; fetching is a separate
authorized source scope approval.

This sends network requests for reference metadata. It does not retrieve full
source bodies, publish notes or write to the KB.

Usage:
    kb-resolve-reference.py "Harris 2003 Trading and Exchanges market microstructure"
    kb-resolve-reference.py "Selye 1936 syndrome produced by nocuous agents" --json --rows 3

Note: an LLM agent may also resolve references with its own web-search tool; this
script gives a deterministic, keyless baseline so any agent resolves consistently.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

# Crossref etiquette: identify the client (mailto helps stay in the polite pool).
# No key required. Set KB_CONTACT_EMAIL to your address; a neutral default keeps
# this tool shareable in public distributions without leaking local addresses.
UA = f"kb-bot/1.0 (+local knowledge-base; mailto:{os.environ.get('KB_CONTACT_EMAIL', 'kb-bot@example.invalid')})"


def crossref(query: str, rows: int):
    api = ("https://api.crossref.org/works?rows=%d&query.bibliographic=%s"
           % (rows, urllib.parse.quote(query)))
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data.get("message", {}).get("items", [])


def open_access(doi: str) -> str | None:
    if not doi:
        return None
    try:
        api = "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        loc = (data.get("best_oa_location") or {})
        return loc.get("pdf_url") or loc.get("landing_page_url")
    except Exception:
        return None


def candidate(item: dict) -> dict:
    doi = item.get("DOI", "")
    title = (item.get("title") or [""])[0]
    container = (item.get("container-title") or [""])[0]
    year = ""
    for k in ("published-print", "published-online", "issued"):
        parts = (item.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0]); break
    authors = ", ".join(
        f"{a.get('family','')}".strip() for a in (item.get("author") or [])[:3]
    )
    doi_url = f"https://doi.org/{doi}" if doi else ""
    return {
        "title": title,
        "container": container,
        "year": year,
        "authors": authors,
        "type": item.get("type", ""),
        "doi_url": doi_url,
        "open_access_url": open_access(doi),
        "score": round(item.get("score", 0.0), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("citation", help="free-text bibliographic string")
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        items = crossref(args.citation, args.rows)
    except Exception as exc:
        print(f"error: crossref lookup failed: {exc}", file=sys.stderr)
        return 1

    if not items:
        print("no candidates found (try a web-search fallback)", file=sys.stderr)
        return 3

    cands = [candidate(it) for it in items]
    if args.json:
        print(json.dumps(cands, indent=2))
    else:
        for i, c in enumerate(cands, 1):
            print(f"[{i}] {c['title']}  ({c['year']}) — {c['authors']}  · {c['container']}")
            if c["open_access_url"]:
                print(f"    open-access: {c['open_access_url']}  (queue this)")
            if c["doi_url"]:
                print(f"    doi/anchor : {c['doi_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
