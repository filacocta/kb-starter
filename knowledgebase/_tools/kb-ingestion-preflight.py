#!/usr/bin/env python3
"""Read-only inventory of exact Approved/Auto-approved queue rows.

This tool is deliberately a preflight, not an ingestion runner.  It takes a
short, non-blocking shared-lock snapshot, uses the authoritative source-note
index (never ``_seen-urls.json``), and rechecks the snapshot before returning
eligible candidates.  A candidate is eligible only when it is an unchecked,
URL-first row with a valid unseen HTTP(S) locator and no active-queue or source
identity ambiguity.  No fetching, extraction, queue writes, or note writes
are performed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
SECTION_NAMES = ("Approved", "Auto-approved")
TOPIC_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HTTP_PREFIX_RE = re.compile(r"^(?:https?://)")
HTTP_PREFIX_ANYCASE_RE = re.compile(r"^(?:https?://)", re.IGNORECASE)
ABS_PDF_RE = re.compile(r"^/[^\s]*\.pdf$", re.IGNORECASE)


def _load_tool(filename: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


# These module-level handles are intentional: synthetic callers can replace a
# helper at the boundary without changing the read-only control flow.
WRITE_GUARD = _load_tool("kb-write-guard.py", "kb_preflight_write_guard")
SEEN = _load_tool("kb-seen.py", "kb_preflight_seen")
QUEUE_PARSE = _load_tool("kb-queue-parse.py", "kb_preflight_queue_parse")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _simple_topic(topic: str) -> bool:
    return bool(isinstance(topic, str) and TOPIC_RE.fullmatch(topic))


def _queue_rows(text: str) -> tuple[list[dict[str, Any]], set[str]]:
    """Return exact unchecked rows and duplicate target section headings."""
    parsed = QUEUE_PARSE.parse_queue(text)
    if parsed.frontmatter_error:
        raise ValueError(parsed.frontmatter_error)
    if parsed.unclosed_fence:
        character, line = parsed.unclosed_fence
        raise ValueError(f"unclosed {character}-fence opened at line {line}")
    if parsed.unclosed_comment:
        raise ValueError(f"unclosed HTML comment opened at line {parsed.unclosed_comment}")
    if parsed.unsupported_html_line:
        raise ValueError(f"unsupported raw HTML block at line {parsed.unsupported_html_line}")
    if parsed.malformed_approval_headings:
        name = parsed.malformed_approval_headings[0]
        expected = next(
            section for section in SECTION_NAMES if section.casefold() == name.casefold()
        )
        raise ValueError(f"noncanonical section heading {name!r}; use {expected!r}")
    rows: list[dict[str, Any]] = []
    for row in parsed.rows:
        if row.section not in SECTION_NAMES or row.mark != " ":
            continue
        rows.append(
            {
                "line": row.line,
                "raw_row": row.raw,
                "section": row.section,
            }
        )
    return rows, set(parsed.duplicate_approval_sections)


def _queue_paths(root: Path, topic: str | None) -> list[Path]:
    topics = root / "topics"
    try:
        if topics.is_symlink():
            raise ValueError("topics directory must not be a symlink")
        if not topics.exists():
            raise ValueError("topics directory is missing")
        if not topics.is_dir():
            raise ValueError("topics path must be a directory")
    except OSError as exc:
        raise ValueError(f"cannot inspect topics directory: {exc}") from exc
    if topic is not None:
        if not _simple_topic(topic):
            raise ValueError("topic must be a canonical lowercase slug")
        topic_dir = topics / topic
        try:
            topic_dir.lstat()
        except FileNotFoundError:
            raise ValueError(f"requested topic directory is missing: {topic}") from None
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"cannot inspect requested topic {topic}: {exc}") from exc
        if topic_dir.is_symlink() or not topic_dir.is_dir():
            raise ValueError(f"requested topic directory is unsafe: {topic}")
        candidate = topic_dir / "reading-list.md"
        try:
            candidate.lstat()
        except FileNotFoundError:
            raise ValueError(f"requested topic queue is missing: {topic}") from None
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"cannot inspect selected queue {candidate}: {exc}") from exc
        if candidate.is_symlink():
            raise ValueError(f"selected queue must not be a symlink: {candidate}")
        return [candidate]
    try:
        entries = sorted(topics.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        raise ValueError("topics directory is missing") from None
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot enumerate topic queues: {exc}") from exc
    paths: list[Path] = []
    for entry in entries:
        try:
            if entry.is_symlink():
                raise ValueError(f"topic directory must not be a symlink: {entry}")
            if entry.is_dir() and not _simple_topic(entry.name):
                raise ValueError(f"topic directory has noncanonical slug: {entry.name}")
            if not entry.is_dir():
                continue
            queue = entry / "reading-list.md"
            if queue.is_symlink():
                raise ValueError(f"topic queue must not be a symlink: {queue}")
            if queue.exists():
                paths.append(queue)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"cannot inspect topic queue {entry}: {exc}") from exc
    return paths


def _source_paths(root: Path) -> set[str]:
    """List source-note paths for membership checks, without reading bodies."""
    paths: set[str] = set()
    try:
        candidates = root.glob("topics/*/sources/*.md")
        for path in candidates:
            paths.add(_relative(root, path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"cannot enumerate source tree: {exc}") from exc
    return paths


def _alias_record(entry: Any) -> dict[str, str]:
    """Project every validated alias field, without trusting raw registry data."""
    fields: dict[str, str] = {}
    for field in ("alias", "source_note", "evidence", "verified"):
        value = getattr(entry, field, None)
        if value is None and isinstance(entry, dict):
            value = entry.get(field)
        if not isinstance(value, str):
            raise ValueError(f"validated alias is missing scalar field {field}")
        fields[field] = value
    return fields


def _source_snapshot(root: Path) -> dict[str, Any]:
    """Read the authoritative index and preserve collision information."""
    # load_index is intentionally used even though iter_origins is also read:
    # it validates the current alias registry and source-note ownership while
    # the multimap prevents scan_index's historical setdefault from hiding
    # duplicate source identities.
    index = SEEN.load_index(root)
    origins: defaultdict[str, set[str]] = defaultdict(set)
    for normalized, source_note in SEEN.iter_origins(root):
        origins[normalized].add(source_note)
    aliases = [_alias_record(entry) for entry in SEEN.load_alias_registry(root)]
    alias_map: defaultdict[str, set[str]] = defaultdict(set)
    for entry in aliases:
        alias_map[SEEN.normalize(entry["alias"])].add(entry["source_note"])
    source_paths = _source_paths(root)
    # Keep a stable, identity-only signature.  It is not a source-body hash
    # and makes no extraction/fidelity claim.
    identity_payload = {
        "index": sorted(index.items()),
        "origins": sorted((key, sorted(value)) for key, value in origins.items()),
        "aliases": aliases,
        "source_paths": sorted(source_paths),
    }
    return {
        "index": index,
        "origins": {key: frozenset(value) for key, value in origins.items()},
        "aliases": aliases,
        "alias_map": {key: frozenset(value) for key, value in alias_map.items()},
        "source_paths": frozenset(source_paths),
        "signature": _sha256_text(json.dumps(identity_payload, sort_keys=True)),
    }


def _global_inbox(root: Path) -> tuple[int, str | None]:
    path = root / "readinglist" / "Links.md"
    try:
        text = WRITE_GUARD.regular_text(path)
    except FileNotFoundError:
        return 0, None
    rows, _ = _queue_rows_all(text)
    return rows, _sha256_text(text)


def _queue_rows_all(text: str) -> tuple[int, None]:
    parsed = QUEUE_PARSE.parse_queue(text)
    if parsed.frontmatter_error:
        raise ValueError(parsed.frontmatter_error)
    if parsed.unclosed_fence:
        character, line = parsed.unclosed_fence
        raise ValueError(f"unclosed {character}-fence opened at line {line}")
    if parsed.unclosed_comment:
        raise ValueError(f"unclosed HTML comment opened at line {parsed.unclosed_comment}")
    if parsed.unsupported_html_line:
        raise ValueError(f"unsupported raw HTML block at line {parsed.unsupported_html_line}")
    return sum(row.mark == " " for row in parsed.rows), None


def _read_queues(root: Path, topic: str | None) -> tuple[list[dict[str, Any]], dict[str, str], set[str]]:
    rows: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    paths = _queue_paths(root, topic)
    for path in paths:
        try:
            text = WRITE_GUARD.guarded_text(path, root)
        except (OSError, RuntimeError, ValueError) as exc:
            rel = _relative(root, path)
            raise ValueError(f"unsafe queue {rel}: {exc}") from exc
        rel = _relative(root, path)
        digest = _sha256_text(text)
        hashes[rel] = digest
        parsed, duplicate_headings = _queue_rows(text)
        for row in parsed:
            row.update({"queue_path": rel, "file_sha256": digest})
            if duplicate_headings:
                row["queue_ambiguous"] = True
                row["queue_ambiguous_reason"] = "duplicate-target-section-heading"
        rows.extend(parsed)
    return rows, hashes, {str(path) for path in _queue_paths(root, topic)}


def _validate_url(locator: str) -> tuple[bool, str | None]:
    """Validate before normalization, including the normalizer's port limit."""
    if not isinstance(locator, str) or not locator or locator != locator.strip():
        return False, "invalid-url-whitespace"
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in locator):
        return False, "invalid-url-whitespace"
    try:
        parts = urllib.parse.urlsplit(locator)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False, "invalid-url"
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or not host:
        return False, "invalid-url"
    if parts.username is not None or parts.password is not None:
        return False, "invalid-url-credentials"
    # kb-seen.normalize intentionally drops ports.  Refuse non-default ports
    # here rather than claiming an unseen identity that may collide elsewhere.
    if port is not None and port != (443 if parts.scheme.lower() == "https" else 80):
        return False, "unsupported-identity-port"
    if parts.netloc.endswith(":"):
        return False, "invalid-url-port"
    if parts.fragment:
        return False, "unsupported-identity-fragment"
    return True, None


def _first_body(row: str) -> str:
    return re.sub(r"^\s*-\s+\[ \]\s*", "", row, count=1)


def _classify_locator(raw_row: str) -> tuple[str, str | None, str | None]:
    """Return (kind, locator, error) without scanning prose for URLs."""
    body = _first_body(raw_row)
    locator = QUEUE_PARSE.primary_locator(raw_row)
    if locator is not None and HTTP_PREFIX_RE.match(locator):
        valid, error = _validate_url(locator)
        return ("url" if valid else "invalid-url", locator, error)
    # A malformed http(s) token still looks like a locator and must not be
    # downgraded to prose.  This also catches ``https://``.
    if HTTP_PREFIX_RE.match(body):
        token = body.split(None, 1)[0] if body.split(None, 1) else body
        _, error = _validate_url(token)
        # A fallback is diagnostic only.  Publication and completion share
        # primary_locator's exact lowercase token contract, so never make a
        # fallback token eligible even if its URL syntax happens to validate.
        return "invalid-url", token, error or "primary-locator-missing"
    if HTTP_PREFIX_ANYCASE_RE.match(body):
        return "unsupported", None, "unsupported-locator-case"
    if locator is not None and ABS_PDF_RE.fullmatch(locator):
        return "local-pdf", locator, None
    # Unsupported local forms and prose deliberately do not infer a locator.
    return "unsupported", None, "unsupported-locator"


def _inspect_local(locator: str) -> tuple[str, str | None]:
    """Inspect only regular-file metadata; never read local PDF payload bytes."""
    try:
        descriptor, _info = WRITE_GUARD._open_regular(
            Path(locator), flags=os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        )
        os.close(descriptor)
        return "present", None
    except FileNotFoundError:
        return "missing", None
    except (OSError, RuntimeError, ValueError) as exc:
        return "error", str(exc)


def _classify_rows(rows: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    queue_keys: defaultdict[str, list[int]] = defaultdict(list)
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        item.setdefault("channel_needs", "unknown")
        kind, locator, error = _classify_locator(item.get("raw_row") or "")
        item.update({"locator_type": kind, "locator": locator})
        if kind == "url":
            normalized = SEEN.normalize(locator)
            item["normalized_url"] = normalized
            queue_keys[normalized].append(index)
            item["dedup"] = {"status": "unknown"}
        elif kind == "local-pdf":
            state, detail = _inspect_local(locator or "")
            item["local_file"] = state
            item["dedup"] = {"status": "unknown", "reason": "local-needs-manual-dedup"}
            if detail:
                item["error"] = detail
        else:
            item["dedup"] = {"status": "unknown"}
        if error:
            item["reason"] = error
        prepared.append(item)

    # Queue ambiguity is independent from source ambiguity.  Both exact and
    # tracking-parameter variants are blocked, and every row in the group is
    # reported rather than silently selecting the first occurrence.
    for key, indexes in queue_keys.items():
        if len(indexes) > 1:
            for index in indexes:
                prepared[index]["queue_ambiguous"] = True
                prepared[index]["reason"] = "queue-ambiguous"

    for item in prepared:
        item.setdefault("eligibility", "ineligible")
        if item.get("locator_type") != "url":
            continue
        normalized = item["normalized_url"]
        direct = set(source["origins"].get(normalized, ()))
        aliases = set(source["alias_map"].get(normalized, ()))
        known = direct | aliases
        if len(known) > 1:
            item["dedup"] = {"status": "ambiguous", "source_notes": sorted(known)}
            item["reason"] = "dedup-ambiguous"
            continue
        if known:
            # This call is intentionally required for the single-known-source
            # case; it validates the target source path instead of trusting a
            # multimap entry alone.
            found = SEEN.source_for_url(item["locator"], source["root"], source["index"])
            if found is None or found not in known:
                raise ValueError("source index disagreed with single-known-source mapping")
            item["dedup"] = {"status": "seen", "source_note": found}
        else:
            found = SEEN.source_for_url(item["locator"], source["root"], source["index"])
            if found is not None:
                item["dedup"] = {"status": "seen", "source_note": found}
            else:
                item["dedup"] = {"status": "unseen"}
        if item.get("queue_ambiguous"):
            item["eligibility"] = "ineligible"
        elif item["dedup"]["status"] == "unseen":
            item["eligibility"] = "eligible-candidate"
            item["reason"] = "unseen"
        else:
            item["eligibility"] = "ineligible"
            item.setdefault("reason", "already-ingested")
    return prepared


def _empty_result(root: Path, topic: str | None, *, state: str, error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": state,
        "kb_root": str(root),
        "topic": topic,
        "global_inbox_unchecked_count": 0,
        "counts": {"topic_unchecked": 0, "eligible_candidates": 0},
        "items": [],
        "eligible_candidates": [],
        "queue_writes": [],
        "note_writes": [],
        "fetch_executed": False,
    }
    if error:
        result["error"] = error
    return result


def _snapshot_changed(initial: dict[str, Any], final: dict[str, Any]) -> bool:
    return initial != final


def preflight(kb_root: Path | str, topic: str | None = None) -> dict[str, Any]:
    """Run the bounded read-only preflight and return JSON-serializable data."""
    requested = Path(kb_root).expanduser()
    try:
        root = WRITE_GUARD.canonical_root(requested)
    except (OSError, RuntimeError, ValueError) as exc:
        return _empty_result(requested, topic, state="STOP", error=str(exc))
    if topic is not None and not _simple_topic(topic):
        return _empty_result(root, topic, state="STOP", error="topic must be a canonical lowercase slug")

    try:
        with WRITE_GUARD.write_lock(root, nonblocking=True):
            try:
                initial_source = _source_snapshot(root)
            except (OSError, RuntimeError, ValueError) as exc:
                return _empty_result(root, topic, state="STOP", error=f"source identity/index error: {exc}")
            initial_source["root"] = root
            try:
                rows, queue_hashes, queue_members = _read_queues(root, topic)
                global_count, global_hash = _global_inbox(root)
            except (OSError, RuntimeError, ValueError) as exc:
                return _empty_result(root, topic, state="STOP", error=f"queue snapshot error: {exc}")
            initial_queues = {
                "hashes": dict(queue_hashes),
                "members": sorted(_relative(root, Path(path)) for path in queue_members),
                "global_hash": global_hash,
            }
            try:
                items = _classify_rows(rows, initial_source)
            except (OSError, RuntimeError, ValueError) as exc:
                return _empty_result(root, topic, state="STOP", error=f"dedup error: {exc}")
            try:
                final_source = _source_snapshot(root)
                final_source["root"] = root
                _, final_queue_hashes, final_queue_members = _read_queues(root, topic)
                _, final_global_hash = _global_inbox(root)
            except (OSError, RuntimeError, ValueError) as exc:
                return _empty_result(root, topic, state="STOP", error=f"snapshot recheck error: {exc}")
            final_queues = {
                "hashes": dict(final_queue_hashes),
                "members": sorted(_relative(root, Path(path)) for path in final_queue_members),
                "global_hash": final_global_hash,
            }
            changed = _snapshot_changed(initial_source, final_source) or initial_queues != final_queues
            if changed:
                for item in items:
                    item["eligibility"] = "ineligible"
                eligible: list[dict[str, Any]] = []
                state = "snapshot_changed"
            else:
                eligible = [item for item in items if item.get("eligibility") == "eligible-candidate"]
                state = "ok"
            counts = {
                "topic_unchecked": len(items),
                "approved_unchecked": sum(item.get("section") == "Approved" for item in items),
                "auto_approved_unchecked": sum(item.get("section") == "Auto-approved" for item in items),
                "eligible_candidates": len(eligible),
                "unseen_valid_urls": sum(item.get("dedup", {}).get("status") == "unseen" for item in items),
                "seen": sum(item.get("dedup", {}).get("status") == "seen" for item in items),
                "queue_ambiguous": sum(bool(item.get("queue_ambiguous")) or item.get("reason") == "queue-ambiguous" for item in items),
                "dedup_ambiguous": sum(item.get("dedup", {}).get("status") == "ambiguous" for item in items),
                "invalid_urls": sum(item.get("locator_type") == "invalid-url" for item in items),
                "local_pdfs": sum(item.get("locator_type") == "local-pdf" for item in items),
                "unsupported": sum(item.get("locator_type") == "unsupported" for item in items),
            }
            return {
                "state": state,
                "kb_root": str(root),
                "topic": topic,
                "global_inbox_unchecked_count": global_count,
                "counts": counts,
                "items": items,
                "eligible_candidates": eligible,
                "queue_writes": [],
                "note_writes": [],
                "fetch_executed": False,
                "alias_mappings": final_source["aliases"],
                "source_index_entry_count": len(final_source["index"]),
            }
    except WRITE_GUARD.LockBusyError as exc:
        return _empty_result(root, topic, state="STOP", error=str(exc))
    except (OSError, RuntimeError, ValueError) as exc:
        return _empty_result(root, topic, state="STOP", error=str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Approved/Auto-approved ingestion preflight.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--topic", help="one canonical lowercase topic slug; default scans all topics")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = preflight(args.kb_root, args.topic)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("state") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
