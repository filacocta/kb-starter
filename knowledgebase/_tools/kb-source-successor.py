#!/usr/bin/env python3
"""Publish one explicitly-reviewed source successor, with a bounded lineage.

This is deliberately a separate, narrow workflow from "kb-commit-source.py".
The ordinary publisher must continue to reject a successful duplicate origin;
this tool accepts only a strict manifest that names one exact predecessor, one
exact replacement, the exact queue row, and an existing canonical review
receipt.  It never fetches, follows redirects, invokes a model/provider, or
uses a force/dedup switch.

The order is important because source completion verifies the *new* source
identity.  The replacement is therefore atomically created first, the exact
queue row is checked off through the normal completion gate second, and only
then is the old note changed to "deprecated".  Those are separate files and
are not cross-file atomic: a returned partial state is a real checkpoint and
is resumed only with the same immutable manifest and bytes.

Manifest (strict JSON, canonicalized into "_tools/successors/<sha>.json")::

    {
      "author": "kb-bot", "version": 1, "operation": "source-successor",
      "old": {
        "path": "topics/example/sources/2026-01-01-old.md",
        "origin": "https://example.test/old",
        "sha256": "<64 hex>", "body_sha256": "<64 hex>",
        "frontmatter_sha256": "<64 hex>", "status": "draft"
      },
      "new": {
        "staged_path": "/safe/staging/new.md",
        "path": "topics/example/sources/2026-09-10-new.md",
        "origin": "https://example.test/new", "sha256": "<64 hex>"
      },
      "queue": {
        "path": "topics/example/reading-list.md",
        "locator": "https://example.test/new", "section": "Approved"
      },
      "review": {"path": "_tools/reviews/<64 hex>.json", "sha256": "<64 hex>"},
      "reason": "The publisher now exposes the maintained document",
      "checkpoint": {"id": "k22-successor-1", "state": "prepared"}
    }

"frontmatter_sha256" is the digest of the old frontmatter with only the
top-level "status" and "superseded_by" lines removed.  It lets resume
verify that title, modified date, media inventory, and every other immutable
frontmatter field survived the lifecycle edit without retaining a source
snapshot in the manifest.

Result states are "SUCCESSOR_COMPLETE",
"SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING",
"SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING", and "STOP".  A
successor of a successor is intentionally out of scope: this version accepts
one predecessor only, and a target that already has successor lifecycle
metadata is rejected.  Any longer chain needs a future explicit manifest
version and migration review.

Usage::

    kb-source-successor.py --kb-root /path/to/knowledgebase \
        --manifest /path/to/prepared-successor.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
_LOADED: dict[str, Any] = {}


class SuccessorError(ValueError):
    """A fail-closed manifest, identity, or lifecycle error."""


class LifecycleWriteError(SuccessorError):
    """The predecessor atomic write failed after its preflight passed."""


def _tool(name: str) -> Any:
    if name not in _LOADED:
        spec = importlib.util.spec_from_file_location(
            f"kb_successor_{name.replace('-', '_')}", HERE / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            raise SuccessorError(f"unable to load helper {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _LOADED[name] = module
    return _LOADED[name]


def _guard() -> Any:
    return _tool("kb-write-guard")


def _seen() -> Any:
    return _tool("kb-seen")


def _validator() -> Any:
    return _tool("kb-validate-source-candidate")


def _review_receipt() -> Any:
    return _tool("kb-review-receipt")


def _checkoff() -> Any:
    return _tool("kb-checkoff")


def _queue_parser() -> Any:
    return _tool("kb-queue-parse")


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SINGLE_LINE_RE = re.compile(r"^[^\r\n\x00]*$")
SOURCE_PARTS = re.compile(r"^[^/\\\.][^/\\]*\.md$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MANIFEST_TOP = {
    "author",
    "version",
    "operation",
    "old",
    "new",
    "queue",
    "review",
    "reason",
    "checkpoint",
}
OLD_FIELDS = {
    "path",
    "origin",
    "sha256",
    "body_sha256",
    "frontmatter_sha256",
    "status",
}
NEW_FIELDS = {"staged_path", "path", "origin", "sha256"}
QUEUE_FIELDS = {"path", "locator", "section"}
REVIEW_FIELDS = {"path", "sha256"}
CHECKPOINT_FIELDS = {"id", "state"}


def _reject_constant(value: str) -> None:
    raise SuccessorError(f"non-finite JSON number is not allowed: {value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise SuccessorError(f"duplicate manifest key: {key}")
        obj[key] = value
    return obj


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
    ) + "\n"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _require_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SuccessorError(f"{label} must be an object")
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise SuccessorError(f"{label}: " + "; ".join(details))
    return value


def _string(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise SuccessorError(f"{label} must be a non-empty string")
    if not SINGLE_LINE_RE.fullmatch(value):
        raise SuccessorError(f"{label} must be a single line without NUL")
    return value


def _sha(value: Any, label: str) -> str:
    value = _string(value, label, max_len=64)
    if not SHA_RE.fullmatch(value):
        raise SuccessorError(f"{label} must be lowercase SHA-256 hex")
    return value


def _safe_relative(value: Any, label: str) -> str:
    value = _string(value, label)
    if value.startswith(("/", "\\")) or "\\" in value:
        raise SuccessorError(f"{label} must be a relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SuccessorError(f"{label} contains an unsafe path component")
    return value


def _canonical_source(root: Path, value: Any, label: str) -> tuple[Path, str]:
    relative = _safe_relative(value, label)
    parts = relative.split("/")
    if len(parts) != 4 or parts[0] != "topics" or parts[2] != "sources":
        raise SuccessorError(f"{label} must be topics/<topic>/sources/<note>.md")
    if not parts[1] or not SOURCE_PARTS.fullmatch(parts[3]):
        raise SuccessorError(f"{label} has an invalid canonical source name")
    target = root / relative
    try:
        if target.resolve(strict=False) != target:
            raise SuccessorError(f"{label} resolves through a symlink")
        if not target.parent.is_dir() or target.parent.resolve(strict=True) != target.parent:
            raise SuccessorError(f"{label} parent directory is missing or unsafe")
    except OSError as exc:
        raise SuccessorError(f"cannot resolve {label}: {exc}") from exc
    return target, relative


def _canonical_queue(root: Path, value: Any, label: str) -> tuple[Path, str]:
    relative = _safe_relative(value, label)
    parts = relative.split("/")
    if len(parts) != 3 or parts[0] != "topics" or parts[2] != "reading-list.md":
        raise SuccessorError(f"{label} must be topics/<topic>/reading-list.md")
    target = root / relative
    try:
        if target.resolve(strict=False) != target:
            raise SuccessorError(f"{label} resolves through a symlink")
    except OSError as exc:
        raise SuccessorError(f"cannot resolve {label}: {exc}") from exc
    return target, relative


def _canonical_review(root: Path, value: Any, label: str) -> tuple[Path, str]:
    relative = _safe_relative(value, label)
    parts = relative.split("/")
    if len(parts) != 3 or parts[0] != "_tools" or parts[1] != "reviews":
        raise SuccessorError(f"{label} must be _tools/reviews/<sha>.json")
    if not re.fullmatch(r"[0-9a-f]{64}\.json", parts[2]):
        raise SuccessorError(f"{label} must name a SHA-256 review receipt")
    target = root / relative
    try:
        if target.resolve(strict=False) != target:
            raise SuccessorError(f"{label} resolves through a symlink")
    except OSError as exc:
        raise SuccessorError(f"cannot resolve {label}: {exc}") from exc
    return target, relative


def _parse_manifest(root: Path, manifest_path: Path) -> tuple[dict[str, Any], str, str]:
    guard = _guard()
    try:
        raw_text = guard.regular_text(manifest_path)
    except Exception as exc:
        raise SuccessorError(f"unsafe or unreadable manifest: {exc}") from exc
    if len(raw_text.encode("utf-8")) > 1_000_000:
        raise SuccessorError("manifest is too large")
    try:
        payload = json.loads(
            raw_text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except SuccessorError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SuccessorError(f"invalid manifest JSON: {exc}") from exc
    payload = _require_keys(payload, MANIFEST_TOP, "manifest")
    if payload["author"] != "kb-bot":
        raise SuccessorError("manifest author must be kb-bot")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise SuccessorError("manifest version must be integer 1")
    if payload["operation"] != "source-successor":
        raise SuccessorError("manifest operation must be source-successor")

    old = _require_keys(payload["old"], OLD_FIELDS, "old")
    new = _require_keys(payload["new"], NEW_FIELDS, "new")
    queue = _require_keys(payload["queue"], QUEUE_FIELDS, "queue")
    review = _require_keys(payload["review"], REVIEW_FIELDS, "review")
    checkpoint = _require_keys(payload["checkpoint"], CHECKPOINT_FIELDS, "checkpoint")

    old_target, old_rel = _canonical_source(root, old["path"], "old.path")
    new_target, new_rel = _canonical_source(root, new["path"], "new.path")
    queue_target, queue_rel = _canonical_queue(root, queue["path"], "queue.path")
    review_target, review_rel = _canonical_review(root, review["path"], "review.path")
    if old_rel == new_rel:
        raise SuccessorError("old and new source paths must differ")
    if old_rel.split("/")[1] != new_rel.split("/")[1]:
        raise SuccessorError("successor must remain in the same topic")
    if queue_rel.split("/")[1] != new_rel.split("/")[1]:
        raise SuccessorError("queue and new source must remain in the same topic")

    staged_raw = _string(new["staged_path"], "new.staged_path", max_len=4096)
    staged_path = Path(staged_raw)
    if not staged_path.is_absolute():
        raise SuccessorError("new.staged_path must be an absolute staging path")
    if staged_path == new_target or staged_path == old_target:
        raise SuccessorError("staging path must not be a canonical source path")

    old_origin = _string(old["origin"], "old.origin")
    new_origin = _string(new["origin"], "new.origin")
    locator = _string(queue["locator"], "queue.locator")
    section = _string(queue["section"], "queue.section", max_len=120)
    if new_origin != locator:
        raise SuccessorError("new.origin must equal queue.locator exactly")
    if not (
        locator.startswith(("http://", "https://"))
        or (locator.startswith("/") and locator.lower().endswith(".pdf"))
    ):
        raise SuccessorError(
            "queue.locator must be an HTTP URL or an absolute PDF path"
        )
    if section not in {"Approved", "Auto-approved"}:
        raise SuccessorError("queue.section must be Approved or Auto-approved")
    _sha(old["sha256"], "old.sha256")
    _sha(old["body_sha256"], "old.body_sha256")
    _sha(old["frontmatter_sha256"], "old.frontmatter_sha256")
    _sha(new["sha256"], "new.sha256")
    if review["sha256"] != Path(review_rel).stem:
        raise SuccessorError("review.sha256 must match review.path filename")
    _sha(review["sha256"], "review.sha256")
    _string(old["status"], "old.status", max_len=32)
    if old["status"] not in {"draft", "review", "final"}:
        raise SuccessorError("old.status must be draft, review, or final")
    reason = _string(payload["reason"], "reason", max_len=1000)
    checkpoint_id = _string(checkpoint["id"], "checkpoint.id", max_len=200)
    if checkpoint["state"] != "prepared":
        raise SuccessorError("checkpoint.state must remain prepared")

    normalized = {
        "author": payload["author"],
        "version": payload["version"],
        "operation": payload["operation"],
        "old": {
            "path": old_rel,
            "origin": old_origin,
            "sha256": old["sha256"],
            "body_sha256": old["body_sha256"],
            "frontmatter_sha256": old["frontmatter_sha256"],
            "status": old["status"],
        },
        "new": {
            "staged_path": str(staged_path),
            "path": new_rel,
            "origin": new_origin,
            "sha256": new["sha256"],
        },
        "queue": {"path": queue_rel, "locator": locator, "section": section},
        "review": {"path": review_rel, "sha256": review["sha256"]},
        "reason": reason,
        "checkpoint": {"id": checkpoint_id, "state": "prepared"},
    }
    canonical_text = _canonical_json(normalized)
    return normalized, _sha_text(canonical_text), canonical_text


def _frontmatter_parts(text: str, path: Path) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise SuccessorError(f"{path}: missing YAML frontmatter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise SuccessorError(f"{path}: unterminated YAML frontmatter")
    return text[4:marker], text[marker + len("\n---\n") :]


def _immutable_frontmatter_sha(raw_frontmatter: str) -> str:
    kept: list[str] = []
    for line in raw_frontmatter.splitlines(keepends=True):
        if re.match(r"^(?:status|superseded_by)\s*:", line):
            continue
        kept.append(line)
    return _sha_text("".join(kept))


def _source_metadata(root: Path, path: Path, text: str) -> tuple[dict[str, Any], str, str]:
    guard = _guard()
    seen = _seen()
    raw_frontmatter, body = _frontmatter_parts(text, path)
    try:
        frontmatter = guard.frontmatter_metadata(text, path)
        identity = seen._identity_frontmatter(text)
    except Exception as exc:
        raise SuccessorError(f"{path}: strict source identity/frontmatter failed: {exc}") from exc
    if not isinstance(frontmatter, dict) or not isinstance(identity, dict):
        raise SuccessorError(f"{path}: source frontmatter must be a mapping")
    if identity.get("author") != "kb-bot" or identity.get("type") != "source":
        raise SuccessorError(f"{path}: source is not owned kb-bot type source")
    if frontmatter.get("author") != "kb-bot" or frontmatter.get("type") != "source":
        raise SuccessorError(f"{path}: source metadata ownership/type mismatch")
    if frontmatter.get("ingest_status", "succeeded") != "succeeded":
        raise SuccessorError(f"{path}: source ingest_status is not succeeded")
    topic = frontmatter.get("topic")
    parts = path.parts
    if topic != parts[-3] or path.parent.name != "sources" or parts[-4] != "topics":
        raise SuccessorError(f"{path}: source topic/path mismatch")
    origin = frontmatter.get("origin")
    if not isinstance(origin, str) or not origin:
        raise SuccessorError(f"{path}: source origin is missing")
    return frontmatter, raw_frontmatter, body


def _source_text(root: Path, path: Path) -> str:
    try:
        return _guard().regular_text(path)
    except Exception as exc:
        raise SuccessorError(f"unsafe or unreadable source {path}: {exc}") from exc


def _validate_old_active(root: Path, manifest: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    old = manifest["old"]
    path = root / old["path"]
    text = _source_text(root, path)
    if _sha_text(text) != old["sha256"]:
        raise SuccessorError("old source full hash is not the manifest pre-transition hash")
    frontmatter, raw_frontmatter, body = _source_metadata(root, path, text)
    if frontmatter.get("origin") != old["origin"]:
        raise SuccessorError("old source origin differs from manifest")
    if frontmatter.get("status") != old["status"]:
        raise SuccessorError("old source status differs from manifest")
    if frontmatter.get("superseded_by") is not None or re.search(
        r"^superseded_by\s*:", raw_frontmatter, flags=re.MULTILINE
    ):
        raise SuccessorError("old source already has successor metadata")
    if _sha_text(body) != old["body_sha256"]:
        raise SuccessorError("old source body hash differs from manifest")
    if _immutable_frontmatter_sha(raw_frontmatter) != old["frontmatter_sha256"]:
        raise SuccessorError("old source immutable frontmatter/media differs from manifest")
    return "active", text, frontmatter, raw_frontmatter, body


def _successor_link(relative: str) -> str:
    if not relative.endswith(".md"):
        raise SuccessorError("successor source path must end in .md")
    return "[[knowledgebase/" + relative[:-3] + "]]"


def _reason_sentence(reason: str) -> str:
    return reason if reason.endswith((".", "!", "?")) else reason + "."


def _banner_pattern(reason: str, link: str) -> re.Pattern[str]:
    sentence = re.escape(_reason_sentence(reason))
    return re.compile(
        r"^> ⚠️ \*\*Deprecated (\d{4}-\d{2}-\d{2}):\*\* "
        + sentence
        + r" Superseded by "
        + re.escape(link)
        + r"\.$"
    )


def _body_without_banner(body: str, reason: str, link: str) -> tuple[str, str]:
    match = re.match(r"^(?:\n)*# [^\n]+\n", body)
    if match is None:
        raise SuccessorError("old source body must begin with an H1 for lifecycle banner")
    rest = body[match.end() :]
    line_end = rest.find("\n")
    if line_end < 0:
        raise SuccessorError("deprecated source banner must be newline-terminated")
    line = rest[:line_end]
    banner = _banner_pattern(reason, link).fullmatch(line)
    if banner is None or not DATE_RE.fullmatch(banner.group(1)):
        raise SuccessorError("old source deprecation banner is not the manifest banner")
    try:
        _dt.date.fromisoformat(banner.group(1))
    except ValueError as exc:
        raise SuccessorError("old source deprecation banner has invalid date") from exc
    return body[: match.end()] + rest[line_end + 1 :], banner.group(1)


def _validate_old_deprecated(
    root: Path, manifest: dict[str, Any]
) -> tuple[str, str, dict[str, Any], str, str]:
    old = manifest["old"]
    new = manifest["new"]
    path = root / old["path"]
    text = _source_text(root, path)
    frontmatter, raw_frontmatter, body = _source_metadata(root, path, text)
    if frontmatter.get("origin") != old["origin"]:
        raise SuccessorError("deprecated predecessor origin differs from manifest")
    if frontmatter.get("status") != "deprecated":
        raise SuccessorError("old source is neither the manifest pre-state nor a valid deprecated state")
    link = _successor_link(new["path"])
    if frontmatter.get("superseded_by") != link:
        raise SuccessorError("deprecated predecessor points to an unexpected successor")
    if _immutable_frontmatter_sha(raw_frontmatter) != old["frontmatter_sha256"]:
        raise SuccessorError("deprecated predecessor immutable frontmatter/media was changed")
    restored, _date = _body_without_banner(body, manifest["reason"], link)
    if _sha_text(restored) != old["body_sha256"]:
        raise SuccessorError("deprecated predecessor body was changed")
    return "deprecated", text, frontmatter, raw_frontmatter, body


def _inspect_old(root: Path, manifest: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    old_path = root / manifest["old"]["path"]
    text = _source_text(root, old_path)
    if _sha_text(text) == manifest["old"]["sha256"]:
        return _validate_old_active(root, manifest)
    return _validate_old_deprecated(root, manifest)


def _validate_new_candidate(root: Path, manifest: dict[str, Any], candidate: str) -> None:
    new = manifest["new"]
    target = root / new["path"]
    if _sha_text(candidate) != new["sha256"]:
        raise SuccessorError("staged replacement hash differs from manifest")
    try:
        result = _validator().validate_text(candidate, target, root)
    except Exception as exc:
        raise SuccessorError(f"replacement source validation failed: {exc}") from exc
    findings = list(getattr(result, "findings", result if isinstance(result, list) else []))
    errors = [f for f in findings if getattr(f, "severity", "block") == "block"]
    if errors:
        raise SuccessorError(
            "replacement source has blocking validation findings: "
            + "; ".join(str(getattr(f, "message", f)) for f in errors)
        )
    old_rel = manifest["old"]["path"]
    unexpected_warnings = []
    for finding in findings:
        if getattr(finding, "severity", "warn") != "warn":
            continue
        code = getattr(finding, "code", "")
        message = str(getattr(finding, "message", finding))
        expected_collision = "origin matches existing source note: " + old_rel
        if code != "origin-collision" or message != expected_collision:
            unexpected_warnings.append(message)
    if unexpected_warnings:
        raise SuccessorError(
            "replacement source has unapproved validation warnings: "
            + "; ".join(unexpected_warnings)
        )
    frontmatter, raw_frontmatter, _body = _source_metadata(root, target, candidate)
    if frontmatter.get("origin") != new["origin"]:
        raise SuccessorError("replacement source origin differs from manifest")
    if frontmatter.get("status") == "deprecated":
        raise SuccessorError("replacement source cannot already be deprecated")
    if frontmatter.get("superseded_by") is not None or re.search(
        r"^superseded_by\s*:", raw_frontmatter, flags=re.MULTILINE
    ):
        raise SuccessorError("replacement source already has successor metadata")
    _assert_replacement_identity_unambiguous(root, manifest, frontmatter)


def _assert_replacement_identity_unambiguous(
    root: Path, manifest: dict[str, Any], frontmatter: dict[str, Any]
) -> None:
    """Reject replacement origin/mirror overlap that could misroute lookups.

    The ordinary candidate validator's collision warning is origin-only.  The
    seen index, however, treats both ``origin`` and ``mirror`` as source
    identity.  A successor must therefore fail closed when either identity
    field of the staged replacement belongs to another source (or a governed
    alias for another source).  The only permitted existing source owner is
    the named predecessor when an origin or mirror identity normalizes to the
    same primary identity as both the old and new origins; this is an explicit
    same-origin successor whose transport identity mirror collapses to that same identity.
    The target itself is allowed while resuming an already-created replacement.
    """
    seen = _seen()
    old_origin = manifest["old"]["origin"]
    new_origin = manifest["new"]["origin"]
    old_normalized = (
        seen.normalize(old_origin)
        if old_origin.startswith(("http://", "https://"))
        else None
    )
    new_normalized = (
        seen.normalize(new_origin)
        if new_origin.startswith(("http://", "https://"))
        else None
    )
    identities: list[tuple[str, str]] = [("origin", new_origin)]
    if "mirror" in frontmatter:
        mirror = frontmatter["mirror"]
        if not isinstance(mirror, str) or not mirror:
            raise SuccessorError("replacement mirror must be a non-empty scalar string")
        identities.extend(("mirror", value) for value in seen.URL_RE.findall(mirror))

    for field, value in identities:
        if not value.startswith(("http://", "https://")):
            continue
        normalized = seen.normalize(value)
        matches = set(seen._matching_source_notes(root, normalized))
        allowed = {manifest["new"]["path"]}
        if (
            field in {"origin", "mirror"}
            and old_normalized == new_normalized == normalized
        ):
            allowed.add(manifest["old"]["path"])
        unexpected = sorted(matches - allowed)
        if unexpected:
            raise SuccessorError(
                "ambiguous successor: replacement identity is ambiguous; "
                f"{field} {value!r} is already claimed through origin/mirror by "
                + ", ".join(unexpected)
            )
        alias_owner = seen.alias_source_for_url(value, root)
        if alias_owner is not None and alias_owner != manifest["new"]["path"]:
            raise SuccessorError(
                "ambiguous successor: replacement identity is ambiguous; "
                f"{field} {value!r} is a governed alias for {alias_owner}"
            )


def _inspect_new(root: Path, manifest: dict[str, Any], candidate: str) -> str:
    target = root / manifest["new"]["path"]
    _validate_new_candidate(root, manifest, candidate)
    try:
        existing = _guard().regular_text(target)
    except FileNotFoundError:
        return "missing"
    except Exception as exc:
        raise SuccessorError(f"unsafe or unreadable replacement target: {exc}") from exc
    if existing != candidate:
        raise SuccessorError("replacement target exists with different bytes")
    return "published"


def _queue_row(root: Path, manifest: dict[str, Any]) -> tuple[str, Any, str]:
    queue = manifest["queue"]
    path = root / queue["path"]
    try:
        text = _guard().regular_text(path)
        parsed = _queue_parser().parse_queue(text)
    except Exception as exc:
        raise SuccessorError(f"queue parse failed: {exc}") from exc
    try:
        parsed.require_approval_safe()
    except Exception as exc:
        raise SuccessorError(f"queue approval safety failed: {exc}") from exc
    matches = [row for row in parsed.rows if row.locator == queue["locator"]]
    if len(matches) != 1:
        raise SuccessorError("queue locator must identify exactly one row")
    row = matches[0]
    if row.section != queue["section"]:
        raise SuccessorError("queue locator is not in the manifest section")
    mark = str(row.mark).lower()
    if mark == "x":
        source_note = "validated-source [[knowledgebase/" + manifest["new"]["path"][:-3] + "]] sha256:" + manifest["new"]["sha256"]
        receipt = "review-receipt sha256:" + manifest["review"]["sha256"]
        raw = str(row.raw)
        if source_note not in raw or receipt not in raw:
            raise SuccessorError("completed queue row lacks exact successor source/review receipt")
        return "complete", row, text
    if mark == " ":
        return "pending", row, text
    raise SuccessorError("queue row must be pending or checked off")


def _review_and_candidate(root: Path, manifest: dict[str, Any]) -> str:
    staged_path = Path(manifest["new"]["staged_path"])
    try:
        candidate = _guard().regular_text(staged_path)
    except Exception as exc:
        raise SuccessorError(f"unsafe or unreadable staged replacement: {exc}") from exc
    _validate_new_candidate(root, manifest, candidate)
    review = manifest["review"]
    review_path = root / review["path"]
    try:
        receipt_sha = _review_receipt().verify_review(
            root,
            review_path,
            candidate_path=manifest["new"]["path"],
            candidate_text=candidate,
            queue_path=manifest["queue"]["path"],
            primary_locator=manifest["queue"]["locator"],
        )
    except Exception as exc:
        raise SuccessorError(f"canonical review receipt failed: {exc}") from exc
    if receipt_sha != review["sha256"]:
        raise SuccessorError("review helper returned a different receipt digest")
    return candidate


def _ensure_store(root: Path) -> Path:
    tools_dir = root / "_tools"
    if not tools_dir.is_dir() or tools_dir.is_symlink():
        raise SuccessorError("root _tools directory is missing or unsafe")
    store = tools_dir / "successors"
    if store.exists():
        if store.is_symlink() or not store.is_dir():
            raise SuccessorError("_tools/successors is not a safe directory")
    else:
        store.mkdir()
    return store


def _canonical_manifest(
    root: Path, digest: str, canonical_text: str, *, allow_create: bool
) -> Path:
    store_candidate = root / "_tools" / "successors"
    if not store_candidate.exists() and not allow_create:
        raise SuccessorError("partial successor has no canonical manifest")
    path = _ensure_store(root) / f"{digest}.json"
    try:
        existing = _guard().regular_text(path)
    except FileNotFoundError:
        if not allow_create:
            raise SuccessorError("partial successor has no canonical manifest")
        try:
            _guard().atomic_create(path, canonical_text, prefix=".successor-manifest-")
        except FileExistsError:
            existing = _guard().regular_text(path)
        else:
            existing = canonical_text
    except Exception as exc:
        raise SuccessorError(f"canonical manifest is unsafe: {exc}") from exc
    if existing != canonical_text:
        raise SuccessorError("canonical manifest bytes differ; refusing resume")
    return path


def _deprecated_text(old_text: str, manifest: dict[str, Any]) -> str:
    raw_frontmatter, body = _frontmatter_parts(old_text, Path(manifest["old"]["path"]))
    lines = raw_frontmatter.splitlines(keepends=True)
    status_indices = [i for i, line in enumerate(lines) if re.match(r"^status\s*:", line)]
    superseded = [i for i, line in enumerate(lines) if re.match(r"^superseded_by\s*:", line)]
    if len(status_indices) != 1 or superseded:
        raise SuccessorError("old source frontmatter cannot receive a single lifecycle transition")
    index = status_indices[0]
    # The selected field may be the last line before the closing YAML marker,
    # so ``splitlines(keepends=True)`` can return it without a terminator.
    # Always terminate the replacement before inserting ``superseded_by``;
    # otherwise the two lifecycle keys would silently become one malformed
    # scalar (and a resume could not prove the old post-state).
    lines[index] = "status: deprecated\n"
    link = _successor_link(manifest["new"]["path"])
    lines.insert(index + 1, f'superseded_by: "{link}"\n')
    date = _dt.date.today().isoformat()
    banner = (
        f"> ⚠️ **Deprecated {date}:** {_reason_sentence(manifest['reason'])} "
        f"Superseded by {link}.\n"
    )
    h1 = re.match(r"^(?:\n)*# [^\n]+\n", body)
    if h1 is None:
        raise SuccessorError("old source body must begin with an H1 for lifecycle banner")
    if body[h1.end() :].startswith("> ⚠️ **Deprecated "):
        raise SuccessorError("old source already has a deprecation banner")
    new_body = body[: h1.end()] + banner + body[h1.end() :]
    return "---\n" + "".join(lines) + "\n---\n" + new_body


def _deprecate_old(root: Path, manifest: dict[str, Any], old_text: str) -> None:
    path = root / manifest["old"]["path"]
    replacement = _deprecated_text(old_text, manifest)
    try:
        _guard().atomic_write(path, replacement)
    except Exception as exc:
        raise LifecycleWriteError(f"old source lifecycle write failed: {exc}") from exc
    _validate_old_deprecated(root, manifest)


def verify_completed_manifest(
    root: Path, manifest_path: Path
) -> tuple[dict[str, Any], str] | None:
    """Verify one canonical manifest's completed post-state read-only.

    The seen-index resolver uses this narrow API instead of maintaining a
    second manifest/frontmatter/review contract.  None means a valid prepared
    manifest is not complete yet; malformed canonical bytes or unsafe paths
    raise and must fail closed.
    """
    root = _guard().canonical_root(root)
    manifest, digest, canonical_text = _parse_manifest(root, manifest_path)
    expected_path = root / "_tools" / "successors" / f"{digest}.json"
    if manifest_path != expected_path:
        raise SuccessorError("successor manifest is not at its canonical digest path")
    try:
        raw = _guard().regular_text(manifest_path)
    except Exception as exc:
        raise SuccessorError(f"canonical successor manifest is unreadable: {exc}") from exc
    if raw != canonical_text or _sha_text(raw) != digest:
        raise SuccessorError("canonical successor manifest bytes/digest are not immutable")
    try:
        _old_state, _old_text, _old_fm, _old_raw, _old_body = _validate_old_deprecated(
            root, manifest
        )
        new_text = _source_text(root, root / manifest["new"]["path"])
        new_fm, new_raw, _new_body = _source_metadata(
            root, root / manifest["new"]["path"], new_text
        )
        if (
            _sha_text(new_text) != manifest["new"]["sha256"]
            or new_fm.get("origin") != manifest["new"]["origin"]
            or new_fm.get("status") == "deprecated"
            or new_fm.get("superseded_by") is not None
            or re.search(r"^superseded_by\s*:", new_raw, flags=re.MULTILINE)
        ):
            return None
        queue_state, _row, _queue_text = _queue_row(root, manifest)
        if queue_state != "complete":
            return None
        review_path = root / manifest["review"]["path"]
        review_sha = _review_receipt().verify_review(
            root,
            review_path,
            candidate_path=manifest["new"]["path"],
            candidate_text=new_text,
            queue_path=manifest["queue"]["path"],
            primary_locator=manifest["queue"]["locator"],
        )
        if review_sha != manifest["review"]["sha256"]:
            return None
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RuntimeError):
        return None
    # Identity ambiguity is a malformed completed lifecycle, not an ordinary
    # incomplete post-state.  Let the seen resolver fail closed instead of
    # silently retaining a lexically-selected source for the overlapping URL.
    _assert_replacement_identity_unambiguous(root, manifest, new_fm)
    return manifest, digest


def _assert_single_predecessor_set(
    root: Path, manifest: dict[str, Any], *, predecessor_active: bool = True
) -> None:
    """Require the exact active predecessor set for the lifecycle phase."""
    old_origin = manifest["old"]["origin"]
    new_origin = manifest["new"]["origin"]
    if not old_origin.startswith(("http://", "https://")):
        return
    seen = _seen()
    old_normalized = seen.normalize(old_origin)
    candidates = seen._matching_source_notes(root, old_normalized)
    active = [
        relative
        for relative in candidates
        if _seen()._lightweight_status(root, relative) != "deprecated"
    ]
    expected = {manifest["old"]["path"]} if predecessor_active else set()
    if (
        new_origin.startswith(("http://", "https://"))
        and seen.normalize(new_origin) == old_normalized
    ):
        expected.add(manifest["new"]["path"])
    if set(active) != expected:
        raise SuccessorError(
            "successor predecessor identity is ambiguous; expected active "
            + ("predecessor and replacement" if predecessor_active else "replacement state")
            + " set only"
        )


def _assert_predecessor_unambiguous_before_publish(
    root: Path, manifest: dict[str, Any], new_state: str
) -> None:
    """Ensure the named predecessor is the only current owner before writes.

    The validator's origin-collision warning is intentionally scoped to the
    exact named predecessor, but warning order is not an identity proof: a
    second source could sort before or after that note.  Check the complete
    active set before publishing the replacement so an ambiguity cannot leave
    a source/queue partial state that is discovered only at deprecation time.
    """
    old_origin = manifest["old"]["origin"]
    new_origin = manifest["new"]["origin"]
    if not (
        old_origin.startswith(("http://", "https://"))
        and new_origin.startswith(("http://", "https://"))
    ):
        return
    seen = _seen()
    old_normalized = seen.normalize(old_origin)
    candidates = seen._matching_source_notes(root, old_normalized)
    active = {
        relative
        for relative in candidates
        if seen._lightweight_status(root, relative) != "deprecated"
    }
    expected = {manifest["old"]["path"]}
    if seen.normalize(new_origin) == old_normalized and new_state == "published":
        expected.add(manifest["new"]["path"])
    if active != expected:
        raise SuccessorError(
            "predecessor identity is ambiguous; expected only the named "
            "predecessor (and an identical existing replacement when resuming)"
        )


def _result(
    status: str,
    state: str,
    manifest_digest: str,
    manifest_rel: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "state": state,
        "manifest_sha256": manifest_digest,
        "manifest": manifest_rel,
        "resume": (
            None
            if status == "SUCCESSOR_COMPLETE"
            else (
                "inspect the named files; do not retry until the blocking condition is resolved"
                if status == "STOP"
                else "retry with the identical manifest and staged bytes"
            )
        ),
    }
    if error:
        result["error"] = error
    return result


def _manifest_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def run_successor(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Execute or safely resume one manifest; return a machine-readable state."""

    try:
        root = _guard().canonical_root(root)
    except Exception as exc:
        return _result("STOP", "root-invalid", "", "", error=str(exc))
    try:
        manifest, digest, canonical_text = _parse_manifest(root, manifest_path)
    except Exception as exc:
        return _result("STOP", "manifest-invalid", "", "", error=str(exc))
    canonical_rel = f"_tools/successors/{digest}.json"
    new_path = root / manifest["new"]["path"]

    try:
        candidate = _review_and_candidate(root, manifest)
        old_state, old_text, _old_fm, _old_raw, _old_body = _inspect_old(root, manifest)
        queue_state, _row, _queue_text = _queue_row(root, manifest)
        new_state = _inspect_new(root, manifest, candidate)
        if old_state == "active":
            _assert_predecessor_unambiguous_before_publish(root, manifest, new_state)
    except Exception as exc:
        return _result("STOP", "preflight-failed", digest, canonical_rel, error=str(exc))

    if old_state == "deprecated":
        if new_state != "published" or queue_state != "complete":
            return _result(
                "STOP",
                "deprecated-predecessor-incomplete",
                digest,
                canonical_rel,
                error="a deprecated predecessor cannot be resumed with missing replacement or queue receipt",
            )
        try:
            with _guard().write_lock(root):
                candidate = _review_and_candidate(root, manifest)
                current_old_state, _old_text, _old_fm, _old_raw, _old_body = _inspect_old(
                    root, manifest
                )
                current_queue_state, _row, _queue_text = _queue_row(root, manifest)
                current_new_state = _inspect_new(root, manifest, candidate)
                if (
                    current_old_state != "deprecated"
                    or current_new_state != "published"
                    or current_queue_state != "complete"
                ):
                    raise SuccessorError(
                        "completed successor post-state changed while acquiring resume lock"
                    )
                _canonical_manifest(root, digest, canonical_text, allow_create=False)
                _assert_single_predecessor_set(
                    root, manifest, predecessor_active=False
                )
                _validate_old_deprecated(root, manifest)
        except Exception as exc:
            return _result(
                "STOP",
                "lifecycle-preflight-failed",
                digest,
                canonical_rel,
                error=str(exc),
            )
        return _result("SUCCESSOR_COMPLETE", "complete", digest, canonical_rel)

    # The first write is guarded by the shared lock.  Re-read all identities
    # under that lock, because the review and preflight above are not locks.
    candidate: str | None = None
    attempted_source_create = False
    try:
        with _guard().write_lock(root):
            candidate = _review_and_candidate(root, manifest)
            old_state, old_text, _old_fm, _old_raw, _old_body = _inspect_old(root, manifest)
            queue_state, _row, _queue_text = _queue_row(root, manifest)
            new_state = _inspect_new(root, manifest, candidate)
            if old_state != "active":
                raise SuccessorError("predecessor changed while acquiring publication lock")
            _assert_predecessor_unambiguous_before_publish(root, manifest, new_state)
            allow_manifest_create = new_state == "missing" and queue_state == "pending"
            _canonical_manifest(
                root, digest, canonical_text, allow_create=allow_manifest_create
            )
            if new_state == "missing":
                attempted_source_create = True
                try:
                    _guard().atomic_create(
                        new_path, candidate, prefix=".successor-source-"
                    )
                except FileExistsError:
                    existing = _guard().regular_text(new_path)
                    if existing != candidate:
                        raise SuccessorError(
                            "replacement target appeared with different bytes"
                        )
                new_state = "published"
    except Exception as exc:
        # If an atomic create raised after installing its hardlink, preserve
        # the fact that only the source phase completed; never guess queue or
        # predecessor state from the exception alone.
        try:
            existing = _guard().regular_text(new_path)
            source_exact = existing == candidate
        except Exception:
            source_exact = False
        if attempted_source_create and candidate is not None and source_exact:
            return _result(
                "SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING",
                "new-published",
                digest,
                canonical_rel,
                error=str(exc),
            )
        return _result("STOP", "source-publication-failed", digest, canonical_rel, error=str(exc))

    # Source completion is intentionally a separate lock acquisition.  The
    # helper appends its exact validated-source/review receipt and regenerates
    # the derived queue; it is not nested under our lock.
    try:
        queue_state, _row, _queue_text = _queue_row(root, manifest)
        if queue_state == "pending":
            ok, checkoff_message = _checkoff().checkoff(
                root / manifest["queue"]["path"],
                manifest["queue"]["locator"],
                "x",
                "successor " + manifest["checkpoint"]["id"],
                root / ".kb-write.lock",
                kb_root=root,
                source_note=manifest["new"]["path"],
                accept_source_warnings=(
                    "successor "
                    + digest
                    + ": the predecessor origin collision is the sole expected warning"
                ),
                review_path=Path(manifest["review"]["path"]),
                required_sections={manifest["queue"]["section"]},
            )
            if not ok:
                return _result(
                    "SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING",
                    "new-published",
                    digest,
                    canonical_rel,
                    error=checkoff_message or "queue completion returned false; predecessor remains active",
                )
    except Exception as exc:
        try:
            current_queue_state, _row, _queue_text = _queue_row(root, manifest)
        except Exception:
            return _result("STOP", "queue-completion-failed", digest, canonical_rel, error=str(exc))
        if current_queue_state == "complete":
            # A checkoff implementation may have installed the receipt and
            # then failed while returning.  Continue to the lifecycle phase.
            queue_state = "complete"
        else:
            return _result(
                "SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING",
                "new-published",
                digest,
                canonical_rel,
                error=str(exc),
            )

    # kb-checkoff mutates only the source-of-truth queue.  Its caller owns
    # regeneration of the derived dashboard, so make that write explicit and
    # leave the predecessor active if the derived update fails.
    try:
        regenerated = _checkoff().regenerate_dive_queue(root, strict=True)
        if regenerated is not True:
            raise RuntimeError("strict dashboard regeneration returned false")
    except Exception as exc:
        return _result(
            "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING",
            "queue-complete",
            digest,
            canonical_rel,
            error=f"queue dashboard regeneration failed: {exc}",
        )

    # Finally transition the predecessor.  Re-read the queue and source under
    # the shared lock, then verify the exact post-state after atomic_write.
    try:
        with _guard().write_lock(root):
            candidate = _review_and_candidate(root, manifest)
            old_state, old_text, _old_fm, _old_raw, _old_body = _inspect_old(root, manifest)
            queue_state, _row, _queue_text = _queue_row(root, manifest)
            new_state = _inspect_new(root, manifest, candidate)
            if new_state != "published" or queue_state != "complete":
                raise SuccessorError("new source or queue receipt is not complete under lock")
            if old_state == "active":
                _assert_single_predecessor_set(root, manifest)
                _deprecate_old(root, manifest, old_text)
            elif old_state != "deprecated":
                raise SuccessorError("predecessor identity changed before lifecycle transition")
            _validate_old_deprecated(root, manifest)
    except LifecycleWriteError as exc:
        try:
            _validate_old_deprecated(root, manifest)
            old_done = True
        except Exception:
            old_done = False
        if old_done:
            return _result("SUCCESSOR_COMPLETE", "complete", digest, canonical_rel)
        return _result(
            "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING",
            "queue-complete",
            digest,
            canonical_rel,
            error=str(exc),
        )
    except Exception as exc:
        return _result("STOP", "lifecycle-preflight-failed", digest, canonical_rel, error=str(exc))

    return _result("SUCCESSOR_COMPLETE", "complete", digest, canonical_rel)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_successor(args.kb_root, args.manifest)
    except Exception as exc:
        result = {"status": "STOP", "state": "fatal", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("status") == "SUCCESSOR_COMPLETE":
        return 0
    return 2 if result.get("status") == "STOP" else 1


if __name__ == "__main__":
    raise SystemExit(main())
