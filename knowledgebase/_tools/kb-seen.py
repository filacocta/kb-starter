#!/usr/bin/env python3
"""Ingestion-level dedup for the KB.

Truth source: the `origin:` and `mirror:` frontmatter of successful source notes
(`topics/*/sources/*.md`). Source notes marked `ingest_status: failed` document
attempted fetches, but they are not treated as ingested. Reading-list `[x]` is
NOT authoritative (it can mean assigned/dropped/routed). A generated JSON cache
(`_seen-urls.json`) is available for explicit snapshots, but read paths rescan
the source notes and governed alias registry so edits/deletions/normalizer
changes cannot leave a stale identity decision.

Usage:
    kb-seen.py --build                  # (re)build _seen-urls.json from source notes
    kb-seen.py --check <url> [<url> ...] # report whether each URL is already ingested
    kb-seen.py --normalize <url>        # print the canonical form (debug)
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "s", "t", "login", "igshid", "spm",
}
X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "fxtwitter.com",
           "vxtwitter.com", "fixupx.com", "api.fxtwitter.com", "nitter.net"}
SEEN_FILE = "_seen-urls.json"
ALIAS_REGISTRY_FILE = "_url-aliases.json"
ALIAS_REGISTRY_VERSION = 1
VALID_INGEST_STATUS = {"succeeded", "failed"}


class AliasRegistryError(ValueError):
    """Raised when the optional governed URL-alias registry is unsafe."""


class UrlAlias:
    """One exact, manually governed URL alias for an existing source note."""

    __slots__ = ("alias", "source_note", "evidence", "verified")

    def __init__(self, alias: str, source_note: str, evidence: str, verified: str):
        self.alias = alias
        self.source_note = source_note
        self.evidence = evidence
        self.verified = verified


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate frontmatter keys."""

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _load_write_guard():
    """Load the shared filesystem guard without relying on a package import."""
    script = Path(__file__).with_name("kb-write-guard.py")
    spec = importlib.util.spec_from_file_location("kb_seen_write_guard", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared KB write guard: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_WRITE_GUARD = _load_write_guard()


def validate_identity_url(url: object, label: str = "URL") -> str:
    """Validate the port portion of one HTTP(S) identity URL.

    ``normalize`` intentionally projects HTTP(S) URLs onto one HTTPS-shaped
    key and therefore does not preserve an explicit port.  A non-default port
    must not enter that projection: it could otherwise collide with the
    default-port endpoint while referring to a different server.  Keep this
    check at the shared identity boundary so lookup, comparison, completion
    and publication all fail closed in the same way.

    The callers that use this helper already own their broader URL-shape
    validation (queue/review/alias contracts).  This helper only enforces the
    identity invariant and the malformed-port cases needed before parsing.
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise AliasRegistryError(f"{label} must be an absolute HTTP(S) URL")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise AliasRegistryError(f"{label} has an invalid port") from exc
    if parts.netloc.endswith(":"):
        raise AliasRegistryError(f"{label} has an invalid port")
    if port is not None and port != (443 if parts.scheme.lower() == "https" else 80):
        raise AliasRegistryError(
            f"{label} nonstandard ports are not supported by the URL identity contract"
        )
    return url


def normalize(url: str) -> str:
    """Return a canonical form for dedup comparison. Conservative: keep
    meaningful query params, treat PDFs and landing pages as distinct."""
    url = url.strip()
    if url.startswith(("http://", "https://")):
        # Never normalize a locator whose port would be discarded.  This is
        # deliberately a rejection, not a fallback to an unseen identity.
        validate_identity_url(url)
    # Normalize trailing punctuation after validating the raw URL.  In
    # particular, a bare IPv6 host legitimately ends in ``]``; validating
    # first prevents the punctuation cleanup from turning it into a newly
    # rejected malformed endpoint.
    url = url.strip(".,);]")
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


def _require_http_url(value: object, label: str) -> str:
    """Validate one concrete URL without fetching or rewriting it."""
    if not isinstance(value, str) or not value.strip():
        raise AliasRegistryError(f"{label} must be a non-empty string")
    if value != value.strip() or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise AliasRegistryError(f"{label} must be a single-line exact URL")
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise AliasRegistryError(f"{label} is not a valid HTTP URL") from exc
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
        raise AliasRegistryError(f"{label} must be an absolute http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise AliasRegistryError(f"{label} must not contain URL credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise AliasRegistryError(f"{label} has an invalid port") from exc
    if parts.netloc.endswith(":"):
        raise AliasRegistryError(f"{label} has an invalid port")
    # The inherited normalizer drops ports; do not broaden an alias endpoint.
    if port is not None and port != (443 if parts.scheme == 'https' else 80):
        raise AliasRegistryError(f"{label} nonstandard ports are not supported by the alias contract")
    # Fragments are not sent to an HTTP server and normalize() intentionally
    # drops them.  Rejecting them keeps an alias one exact locator rather than
    # silently broadening it to every fragment on the page.
    if parts.fragment:
        raise AliasRegistryError(f"{label} must not contain a URL fragment")
    return value


def canonical_source_note(source_note: str, *, require_suffix: bool = False) -> str:
    """Return a safe KB-relative source-note path.

    Registry entries use the strict ``.md`` form.  Other callers may retain
    the historical optional suffix accepted by completion receipts.
    """
    if not isinstance(source_note, str) or not source_note:
        raise AliasRegistryError("source_note must be a non-empty string")
    if any(character in source_note for character in "\r\n\x00"):
        raise AliasRegistryError("source_note must be single-line")
    relative = source_note.removeprefix("knowledgebase/")
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise AliasRegistryError("source_note must be a KB-relative POSIX path")
    parts = tuple(relative.split("/"))
    if (
        len(parts) != 4
        or any(not part or part in {".", ".."} for part in parts)
        or parts[0] != "topics"
        or parts[2] != "sources"
        or (require_suffix and not parts[3].endswith(".md"))
        or (not require_suffix and parts[3].endswith("/"))
    ):
        raise AliasRegistryError("source_note must be topics/<topic>/sources/<note>.md")
    if not parts[3].endswith(".md"):
        if require_suffix:
            raise AliasRegistryError("source_note must end in .md")
        relative += ".md"
    return relative


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


def _strict_frontmatter(text: str) -> dict[str, object]:
    """Parse a source target's YAML without accepting malformed structure."""
    if not text.startswith("---\n"):
        raise AliasRegistryError("alias source_note lacks YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise AliasRegistryError("alias source_note has unterminated YAML frontmatter")
    raw = text[4:end]
    try:
        parsed = yaml.load(raw, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise AliasRegistryError(f"alias source_note has invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise AliasRegistryError("alias source_note frontmatter must be a mapping with string keys")
    return parsed


_IDENTITY_FIELDS = {"author", "type", "origin", "mirror", "ingest_status"}
_TOP_LEVEL_KEY_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*|'[^'\r\n]*'|\"[^\"\r\n]*\")\s*:(?P<value>.*)$"
)
_ANY_IDENTITY_KEY_RE = re.compile(
    r"^\s*(?:-\s*)?(?P<key>['\"]?(?:author|type|origin|mirror|ingest_status)['\"]?)\s*:",
    re.IGNORECASE,
)


def _identity_key(raw_key: str) -> str:
    """Return one exact identity key, rejecting case/quote ambiguity."""
    if raw_key in _IDENTITY_FIELDS:
        return raw_key
    if len(raw_key) >= 2 and raw_key[0] == raw_key[-1] and raw_key[0] in {"'", '"'}:
        quoted = raw_key[1:-1]
        if quoted in _IDENTITY_FIELDS:
            return quoted
    if raw_key.strip("'\"").casefold() in _IDENTITY_FIELDS:
        raise AliasRegistryError(
            f"source identity field {raw_key!r} has non-canonical case or quoting"
        )
    return ""


def _decode_identity_value(field: str, raw_value: str, line_number: int) -> object:
    """YAML-decode one selected value while refusing nested structures."""
    value_text = raw_value.strip()
    if value_text.startswith(("|", ">")):
        raise AliasRegistryError(
            f"source identity field {field!r} uses an ambiguous multiline value at line {line_number}"
        )
    try:
        value = yaml.load(value_text, Loader=_UniqueKeyLoader) if value_text else None
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise AliasRegistryError(
            f"source identity field {field!r} has invalid YAML at line {line_number}: {exc}"
        ) from exc
    if isinstance(value, (dict, list, tuple, set)):
        raise AliasRegistryError(
            f"source identity field {field!r} must be one top-level scalar at line {line_number}"
        )
    return value


def _identity_frontmatter(text: str) -> dict[str, object]:
    """Project only source identity fields from legacy frontmatter.

    Historical source notes can contain prose fields that are not valid YAML
    scalars (for example an unquoted description containing a colon).  Those
    fields are not identity authority.  The selected identity fields remain
    strict: each must be a unique top-level scalar, and nested/list forms are
    rejected rather than guessed.
    """
    if not text.startswith("---\n"):
        raise AliasRegistryError("source note lacks YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise AliasRegistryError("source note has unterminated YAML frontmatter")
    metadata: dict[str, object] = {}
    seen: set[str] = set()
    selected_continuation = False
    for line_number, line in enumerate(text[4:end].splitlines(), 1):
        stripped = line.strip()
        if not stripped or line.lstrip().startswith("#"):
            continue
        if selected_continuation and line[0].isspace():
            raise AliasRegistryError(
                "source identity field has an ambiguous indented continuation "
                f"at line {line_number}"
            )
        if not line[0].isspace():
            selected_continuation = False
        match = _TOP_LEVEL_KEY_RE.match(line)
        if match is not None:
            field = _identity_key(match.group("key"))
            if field in _IDENTITY_FIELDS:
                if field in seen:
                    raise AliasRegistryError(
                        f"source identity field {field!r} is duplicated at line {line_number}"
                    )
                seen.add(field)
                metadata[field] = _decode_identity_value(
                    field, match.group("value"), line_number
                )
                selected_continuation = True
                continue
        nested = _ANY_IDENTITY_KEY_RE.match(line)
        if nested is not None and _identity_key(nested.group("key")) in _IDENTITY_FIELDS:
            field = _identity_key(nested.group("key"))
            raise AliasRegistryError(
                f"source identity field {field!r} must be a unique top-level scalar at line {line_number}"
            )
    return metadata


def _canonical_kb_root(kb_root: Path | str) -> Path:
    """Return a checked KB root, preserving failures instead of re-rooting."""
    try:
        return _WRITE_GUARD.canonical_root(kb_root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise AliasRegistryError(f"unsafe source index KB root: {exc}") from exc


def _read_source_note(kb_root: Path, note: Path) -> tuple[str, dict[str, object]]:
    """Read one source candidate through the shared regular-file guard."""
    try:
        relative = canonical_source_note(
            note.relative_to(kb_root).as_posix(), require_suffix=True
        )
    except (ValueError, AliasRegistryError) as exc:
        raise AliasRegistryError(f"unsafe source index path: {note}") from exc
    try:
        text = _WRITE_GUARD.regular_text(note)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AliasRegistryError(f"unsafe source index note {relative}: {exc}") from exc
    try:
        metadata = _identity_frontmatter(text)
    except AliasRegistryError as exc:
        raise AliasRegistryError(f"unsafe source index note {relative}: {exc}") from exc
    return relative, metadata


def _owned_source_note(
    kb_root: Path, source_note: str
) -> tuple[str, dict[str, object]] | None:
    """Validate a source-note mapping before it can suppress a queue item."""
    relative = canonical_source_note(source_note, require_suffix=True)
    note = kb_root / Path(*relative.split("/"))
    relative, metadata = _read_source_note(kb_root, note)
    if metadata.get("type") != "source":
        return None
    status = metadata.get("ingest_status", "succeeded")
    if not isinstance(status, str):
        raise AliasRegistryError(
            f"unsafe source index note {relative}: ingest_status must be a string"
        )
    if status not in VALID_INGEST_STATUS:
        raise AliasRegistryError(
            f"unsafe source index note {relative}: ingest_status must be one of "
            f"{sorted(VALID_INGEST_STATUS)}"
        )
    # Failed receipts are historical attempts, not ingested sources.  Keep
    # this exclusion ahead of ownership so the existing failure-note
    # semantics remain unchanged.
    if status == "failed":
        return None
    if metadata.get("author") != "kb-bot":
        raise AliasRegistryError(
            f"unsafe source index note {relative}: source ownership requires author: kb-bot"
        )
    return relative, metadata


def _registry_path(kb_root: Path) -> Path:
    return kb_root / ALIAS_REGISTRY_FILE


def _canonical_registry_target(kb_root: Path, source_note: str) -> tuple[Path, str]:
    kb_root = _canonical_kb_root(kb_root)
    relative = canonical_source_note(source_note, require_suffix=True)
    target = kb_root / Path(*relative.split("/"))
    try:
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AliasRegistryError(f"alias source_note target is not resolvable: {relative}") from exc
    try:
        resolved.relative_to(kb_root)
    except ValueError as exc:
        raise AliasRegistryError(f"alias source_note escapes the KB root: {relative}") from exc
    if target.is_symlink() or resolved != target:
        raise AliasRegistryError(f"alias source_note must not be a symlink: {relative}")
    if not target.is_file():
        raise AliasRegistryError(f"alias source_note must be a regular file: {relative}")
    return target, relative


def _successful_source_metadata(kb_root: Path, source_note: str) -> tuple[Path, str, dict[str, object]]:
    target, relative = _canonical_registry_target(kb_root, source_note)
    try:
        text = _WRITE_GUARD.regular_text(target)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AliasRegistryError(f"unsafe alias source target {relative}: {exc}") from exc
    fm = _strict_frontmatter(text)
    if fm.get("author") != "kb-bot" or fm.get("type") != "source":
        raise AliasRegistryError(f"alias source_note is not an owned source note: {relative}")
    if fm.get("ingest_status", "succeeded") != "succeeded" or fm.get("status") == "deprecated":
        raise AliasRegistryError(f"alias source_note must be a successful, non-deprecated source: {relative}")
    origin_value = fm.get("origin", "")
    if not isinstance(origin_value, str):
        raise AliasRegistryError(f"origin in {relative} must be a scalar HTTP URL")
    origin = origin_value.strip()
    _require_http_url(origin, f"origin in {relative}")
    mirror = fm.get("mirror", "")
    if mirror and not isinstance(mirror, str):
        raise AliasRegistryError(f"mirror in {relative} must be a scalar HTTP URL")
    if mirror:
        _require_http_url(mirror, f"mirror in {relative}")
    return target, relative, {key: str(value) if isinstance(value, (str, int, float, bool)) else value for key, value in fm.items()}


def load_alias_registry(kb_root: Path) -> tuple[UrlAlias, ...]:
    """Read and strictly validate the optional governed alias registry.

    The registry is deliberately read-only from this tool.  An absent file is
    equivalent to an empty registry for backwards compatibility.  Once the
    file exists, every field and target is validated before any alias can be
    used; malformed state fails closed instead of being ignored.
    """
    kb_root = _canonical_kb_root(kb_root)
    path = _registry_path(kb_root)
    if path.is_symlink():
        raise AliasRegistryError("URL alias registry must be a regular non-symlink file")
    if not path.exists():
        return ()
    try:
        resolved_registry = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AliasRegistryError("URL alias registry is not resolvable") from exc
    if path.is_symlink() or resolved_registry != path or not path.is_file():
        raise AliasRegistryError("URL alias registry must be a regular non-symlink file")
    try:
        registry_text = _WRITE_GUARD.regular_text(path)
        payload = json.loads(registry_text)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise AliasRegistryError(f"unsafe URL alias registry: {exc}") from exc
    if not isinstance(payload, dict):
        raise AliasRegistryError("URL alias registry must be a JSON object")
    if set(payload) != {"author", "version", "entries"}:
        raise AliasRegistryError("URL alias registry must contain exactly author, version, and entries")
    if payload.get("author") != "kb-bot":
        raise AliasRegistryError("URL alias registry author must be kb-bot")
    version = payload.get("version")
    if type(version) is not int or version != ALIAS_REGISTRY_VERSION:
        raise AliasRegistryError(f"URL alias registry version must be {ALIAS_REGISTRY_VERSION}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise AliasRegistryError("URL alias registry entries must be a list")

    parsed: list[UrlAlias] = []
    seen_aliases: dict[str, int] = {}
    # Existing source origins and mirrors are checked before aliases are
    # admitted.  A legacy alias may never silently turn a real origin or
    # transport mirror into a second identity.
    source_urls: dict[str, set[str]] = {}
    for norm, relative in iter_origins(kb_root):
        source_urls.setdefault(norm, set()).add(relative)

    for index, raw in enumerate(entries, 1):
        if not isinstance(raw, dict) or set(raw) != {"alias", "source_note", "evidence", "verified"}:
            raise AliasRegistryError(
                f"URL alias registry entry {index} must contain exactly alias, source_note, evidence, and verified"
            )
        alias = _require_http_url(raw.get("alias"), f"alias entry {index}")
        source_note = raw.get("source_note")
        if not isinstance(source_note, str):
            raise AliasRegistryError(f"alias entry {index} source_note must be a string")
        # Normalize only the optional vault prefix.  The alias itself remains
        # the exact URL supplied by the maintainer; no host rewrite or body
        # scraping is performed here.
        source_note = canonical_source_note(source_note, require_suffix=True)
        evidence = raw.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip() or any(c in evidence for c in "\r\n\x00"):
            raise AliasRegistryError(f"alias entry {index} evidence must be non-empty single-line text")
        verified = raw.get("verified")
        if not isinstance(verified, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified):
            raise AliasRegistryError(f"alias entry {index} verified must be an ISO date (YYYY-MM-DD)")
        try:
            dt.date.fromisoformat(verified)
        except ValueError as exc:
            raise AliasRegistryError(f"alias entry {index} verified is not a real ISO date") from exc

        target, relative, fm = _successful_source_metadata(kb_root, source_note)
        del target  # Target validation above is intentional; callers use relative.
        key = normalize(alias)
        if key in seen_aliases:
            raise AliasRegistryError(
                f"URL alias collision: entry {index} normalizes to entry {seen_aliases[key]}"
            )
        seen_aliases[key] = index
        if key in source_urls:
            existing = ", ".join(sorted(source_urls[key]))
            raise AliasRegistryError(f"URL alias collides with successful source origin/mirror: {existing}")
        target_urls = [fm.get("origin", ""), fm.get("mirror", "")]
        for target_url in target_urls:
            if target_url and key == normalize(target_url):
                raise AliasRegistryError(f"URL alias duplicates its target source locator: {relative}")
        parsed.append(UrlAlias(alias=alias, source_note=relative, evidence=evidence.strip(), verified=verified))
    return tuple(parsed)


def alias_index(kb_root: Path) -> dict[str, str]:
    """Return normalized alias keys to validated source-note paths."""
    return {normalize(entry.alias): entry.source_note for entry in load_alias_registry(kb_root)}


def alias_source_for_url(url: str, kb_root: Path) -> str | None:
    """Return the governed source-note target for one URL alias, if any."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    validate_identity_url(url)
    return alias_index(kb_root).get(normalize(url))


def iter_origins(kb_root: Path):
    """Yield (normalized_url, source_note_relpath) for every URL found in the
    origin:/mirror: frontmatter of successful source notes. Handles messy
    values: multiple URLs in one scalar, local paths (skipped), etc."""
    kb_root = _canonical_kb_root(kb_root)
    for note in sorted(kb_root.glob("topics/*/sources/*.md")):
        rel, fm = _read_source_note(kb_root, note)
        if fm.get("type") != "source":
            continue
        status = fm.get("ingest_status", "succeeded")
        if not isinstance(status, str):
            raise AliasRegistryError(
                f"unsafe source index note {rel}: ingest_status must be a string"
            )
        if status not in VALID_INGEST_STATUS:
            raise AliasRegistryError(
                f"unsafe source index note {rel}: ingest_status must be one of "
                f"{sorted(VALID_INGEST_STATUS)}"
            )
        if status == "failed":
            continue
        if fm.get("author") != "kb-bot":
            raise AliasRegistryError(
                f"unsafe source index note {rel}: source ownership requires author: kb-bot"
            )
        for field in ("origin", "mirror"):
            raw = fm.get(field, "")
            if field in fm and not isinstance(raw, str):
                raise AliasRegistryError(
                    f"unsafe source index note {rel}: {field} must be a scalar string"
                )
            for url in URL_RE.findall(raw):
                yield normalize(url), rel


def scan_index(kb_root: Path) -> dict[str, str]:
    """Build an in-memory index from the authoritative source notes.

    This deliberately performs no writes.  Pre-fetch callers use it when the
    on-disk cache is absent or stale, because an incorrect "unseen" result
    risks creating a second immutable source record.
    """
    kb_root = _canonical_kb_root(kb_root)
    index, candidates, statuses = _build_origin_index(kb_root)
    # A governed one-predecessor lifecycle projects the active replacement
    # into read-time identity lookups while retaining the positive seen
    # result.  The ordinary publisher's duplicate-origin scan is separate and
    # remains unchanged, so this does not become a generic dedup override.
    successor_map = _completed_successor_map(kb_root, candidates, statuses)
    for (norm, predecessor), replacement in successor_map.items():
        selected = index.get(norm)
        if selected == predecessor:
            index[norm] = replacement
        elif selected is not None and selected != replacement:
            raise AliasRegistryError(
                "successor manifest conflicts with the selected source identity: "
                + norm
            )
    # Alias entries are validated against their successful source targets and
    # all existing origins before being added.  Existing historical origin
    # collisions remain grandfathered in the ordinary index; a new alias that
    # collides with one fails closed in load_alias_registry().
    for norm, rel in alias_index(kb_root).items():
        existing = index.get(norm)
        if existing is not None:
            raise AliasRegistryError(
                f"URL alias collides with existing source identity: {norm} -> {existing}"
            )
        index[norm] = rel
    return index


def build_index(kb_root: Path) -> dict[str, str]:
    """Rebuild and persist the optional lookup cache."""
    kb_root = _canonical_kb_root(kb_root)
    try:
        with _WRITE_GUARD.write_lock(kb_root):
            index = scan_index(kb_root)
            cache_text = json.dumps(index, indent=2, sort_keys=True) + "\n"
            cache_path = kb_root / SEEN_FILE
            if cache_path.exists():
                _WRITE_GUARD.atomic_write(cache_path, cache_text, prefix=".kb-seen-")
            else:
                _WRITE_GUARD.atomic_create(cache_path, cache_text, prefix=".kb-seen-")
            return index
    except _WRITE_GUARD.WriteGuardError as exc:
        raise AliasRegistryError(f"unsafe seen-index cache: {exc}") from exc


def cache_is_fresh(kb_root: Path, cache_path: Path) -> bool:
    """Whether every successful-source candidate predates the cache.

    Source notes are the truth source.  Checking all candidate mtimes is cheap
    relative to parsing the entire KB and avoids trusting a cache written
    before a concurrent ingestion committed a new immutable source record.
    """
    try:
        cache_mtime_ns = cache_path.stat().st_mtime_ns
    except OSError:
        return False
    try:
        return all(note.stat().st_mtime_ns <= cache_mtime_ns
                   for note in kb_root.glob("topics/*/sources/*.md"))
    except OSError:
        # A note changed or disappeared during the scan.  Fall back to the
        # authoritative scan rather than returning a potentially stale cache.
        return False


def load_index(kb_root: Path) -> dict[str, str]:
    # Always scan the authoritative notes and optional alias registry.  This
    # is intentionally a read-only cache bypass: a mapping cache cannot safely
    # encode normalizer changes, registry edits, or deleted source notes
    # without adding another durable versioning contract.
    return scan_index(kb_root)


_SUCCESSOR_MANIFEST_DIR = Path("_tools/successors")
_SUCCESSOR_MODULE: Any = None


def _successor_module() -> Any:
    global _SUCCESSOR_MODULE
    if _SUCCESSOR_MODULE is None:
        key = "kb_seen_source_successor"
        spec = importlib.util.spec_from_file_location(
            key, Path(__file__).with_name("kb-source-successor.py")
        )
        if spec is None or spec.loader is None:
            raise AliasRegistryError("cannot load source successor verifier")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
        _SUCCESSOR_MODULE = module
    return _SUCCESSOR_MODULE


def _matching_source_notes(
    kb_root: Path, normalized: str, candidates: dict[str, list[str]] | None = None
) -> list[str]:
    """Return source-note paths carrying one normalized origin or mirror."""
    if candidates is not None:
        return list(candidates.get(normalized, []))
    matches: set[str] = set()
    for candidate_normalized, relative in iter_origins(kb_root):
        if candidate_normalized == normalized:
            matches.add(relative)
    return sorted(matches)


def _lightweight_status(kb_root: Path, relative: str) -> str:
    """Read lifecycle status while preserving legacy identity parsing."""
    path = kb_root / Path(*relative.split("/"))
    try:
        text = _WRITE_GUARD.regular_text(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AliasRegistryError(f"unsafe successor source {relative}: {exc}") from exc
    metadata = _frontmatter(text)
    status = metadata.get("status", "draft")
    if not isinstance(status, str):
        raise AliasRegistryError(f"successor source {relative}: status must be scalar text")
    return status


def _build_origin_index(
    kb_root: Path,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """Build identity, candidate, and lifecycle maps in one source scan."""
    index: dict[str, str] = {}
    candidates: dict[str, list[str]] = {}
    for normalized, relative in iter_origins(kb_root):
        candidates.setdefault(normalized, [])
        if relative not in candidates[normalized]:
            candidates[normalized].append(relative)
        index.setdefault(normalized, relative)
    for values in candidates.values():
        values.sort()
    relatives = {relative for values in candidates.values() for relative in values}
    statuses = {
        relative: _lightweight_status(kb_root, relative)
        for relative in sorted(relatives)
    }
    return index, candidates, statuses


def _completed_successor_map(
    kb_root: Path,
    candidates: dict[str, list[str]],
    statuses: dict[str, str],
) -> dict[tuple[str, str], str]:
    """Return only completed manifest-bound predecessor-to-target mappings."""
    store = kb_root / _SUCCESSOR_MANIFEST_DIR
    # ``Path.exists()`` is false for a broken symlink.  Inspect the link bit
    # first so a missing target cannot downgrade an unsafe manifest store to
    # the ordinary "no successor records" case.
    if store.is_symlink():
        raise AliasRegistryError("successor manifest store is not a safe directory")
    if not store.exists():
        return {}
    if not store.is_dir():
        raise AliasRegistryError("successor manifest store is not a safe directory")

    records: list[dict[str, object]] = []
    for path in sorted(store.glob("*.json")):
        try:
            verified = _successor_module().verify_completed_manifest(kb_root, path)
        except Exception as exc:
            raise AliasRegistryError(
                f"successor manifest validation failed for {path.name}: {exc}"
            ) from exc
        if verified is not None:
            records.append(verified[0])

    old_paths = {str(record["old"]["path"]) for record in records}
    if any(str(record["new"]["path"]) in old_paths for record in records):
        raise AliasRegistryError("successor manifest chain is longer than one predecessor")

    resolved: dict[tuple[str, str], str] = {}
    targets: dict[str, tuple[str, str]] = {}
    for record in records:
        old = record["old"]
        new = record["new"]
        old_origin = str(old["origin"])
        if not old_origin.startswith(("http://", "https://")):
            continue
        old_normalized = normalize(old_origin)
        old_path = str(old["path"])
        new_origin = str(new["origin"])
        if old_path not in candidates.get(old_normalized, []):
            continue

        active = [
            relative
            for relative in candidates.get(old_normalized, [])
            if statuses.get(relative, "draft") != "deprecated"
        ]
        if new_origin.startswith(("http://", "https://")) and normalize(new_origin) == old_normalized:
            if active != [str(new["path"])]:
                raise AliasRegistryError(
                    "ambiguous successor identity: expected one active replacement"
                )
        elif active:
            raise AliasRegistryError(
                "ambiguous predecessor identity: another active source retains the old URL"
            )

        key = (old_normalized, old_path)
        target = str(new["path"])
        if key in resolved or target in targets:
            raise AliasRegistryError("multiple successor manifests resolve one identity")
        resolved[key] = target
        targets[target] = key
    return resolved


def _resolve_bounded_successor(
    kb_root: Path,
    normalized: str,
    selected: str,
    *,
    candidates: dict[str, list[str]] | None = None,
    statuses: dict[str, str] | None = None,
    successor_map: dict[tuple[str, str], str] | None = None,
) -> str:
    """Resolve only a completed, canonical one-predecessor manifest."""
    if successor_map is None:
        _index, built_candidates, built_statuses = _build_origin_index(kb_root)
        candidates = built_candidates if candidates is None else candidates
        statuses = built_statuses if statuses is None else statuses
        successor_map = _completed_successor_map(kb_root, candidates, statuses)
    return successor_map.get((normalized, selected), selected)


def source_for_url(url: str, kb_root: Path, index: dict[str, str] | None = None) -> str | None:
    """Return the successful source-note path matching a URL or alias."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    # Validate the query before loading/scanning the index.  This prevents an
    # unsupported endpoint from being compared against a caller-supplied
    # snapshot or from triggering unrelated source-index work first.
    validate_identity_url(url, "queried URL")
    supplied_index = index is not None
    if index is None:
        index = load_index(kb_root)
    source_note = index.get(normalize(url))
    if source_note is None:
        return None
    root = _canonical_kb_root(kb_root)
    validated = _owned_source_note(root, source_note)
    if validated is None:
        return None
    selected = validated[0]
    if supplied_index:
        # A caller-provided index may predate canonical successor projection.
        # Rebuild the origin map only for a deprecated selected record; the
        # normal load_index path has already done this once.
        try:
            text = _WRITE_GUARD.regular_text(root / Path(*selected.split("/")))
            status = _frontmatter(text).get("status", "draft")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise AliasRegistryError(f"unsafe source index note {selected}: {exc}") from exc
        if status == "deprecated":
            _built, candidates, statuses = _build_origin_index(root)
            successor_map = _completed_successor_map(root, candidates, statuses)
            return successor_map.get((normalize(url), selected), selected)
    return selected


def locators_match(left: str, right: str, kb_root: Path, index: dict[str, str] | None = None) -> bool:
    """Compare queue/source URL locators using origins plus governed aliases."""
    for value, label in ((left, "left locator"), (right, "right locator")):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            validate_identity_url(value, label)
    if not (left.startswith(("http://", "https://")) and right.startswith(("http://", "https://"))):
        return left == right
    if index is None:
        index = load_index(kb_root)
    if normalize(left) == normalize(right):
        return True
    left_source = source_for_url(left, kb_root, index)
    right_source = source_for_url(right, kb_root, index)
    return left_source is not None and left_source == right_source


def url_matches_source(url: str, source_note: str, origin: str, kb_root: Path) -> bool:
    """Whether *url* identifies the given source's origin or exact alias."""
    if not (url.startswith(("http://", "https://")) and origin.startswith(("http://", "https://"))):
        return False
    # Validate both sides before any normalized comparison.  In particular, a
    # valid queue locator must not be allowed to match a historical or staged
    # origin whose non-default port would be discarded by ``normalize``.
    validate_identity_url(url, "queried URL")
    validate_identity_url(origin, "source origin")
    relative = canonical_source_note(source_note)
    if normalize(url) == normalize(origin):
        load_alias_registry(kb_root)
        return True
    return alias_source_for_url(url, kb_root) == relative


def is_ingested(url: str, kb_root: Path, index: dict[str, str] | None = None) -> str | None:
    """Return the source-note path if `url` is already ingested, else None."""
    return source_for_url(url, kb_root, index)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KB ingestion-level dedup helper.")
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--build", action="store_true", help="rebuild the _seen-urls.json cache")
    p.add_argument("--check", nargs="+", metavar="URL", help="report whether URL(s) are ingested")
    p.add_argument("--normalize", nargs="+", metavar="URL", help="print canonical form (debug)")
    args = p.parse_args(argv)
    kb_root = Path(args.kb_root).expanduser()

    if args.normalize:
        for u in args.normalize:
            print(f"{u}\n  -> {normalize(u)}")
        return 0

    try:
        if args.build:
            idx = build_index(kb_root)
            print(json.dumps({"built": SEEN_FILE, "unique_urls": len(idx)}, indent=2))
            return 0

        if args.check:
            idx = load_index(kb_root)
            out = {u: is_ingested(u, kb_root, idx) for u in args.check}
            print(json.dumps({"ingested": out}, indent=2))
            return 0
    except (AliasRegistryError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
