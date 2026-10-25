#!/usr/bin/env python3
"""Retain and verify bounded, immutable KB source-review receipts.

This tool records a review decision; it does not attest to truth, fetch a
source, call a model, publish a note, or complete a queue. The internal
:func:`verify_review` adapter binds caller-validated candidate bytes and queue
context; it is not a standalone publication validator. Receipts are canonical JSON files under ``_tools/reviews`` and are
never overwritten.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib.parse import urlsplit


STORE = Path("_tools/reviews")
EVIDENCE_STORE = Path("_tools/evidence")
MAX_REVIEW_BYTES = 1 * 1024 * 1024
MAX_FIELD_CHARS = 8192
MAX_LIST_ITEMS = 512
MAX_EVIDENCE_ITEMS = 128
MAX_JSON_DEPTH = 32
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_FIELDS = {
    "author",
    "version",
    "candidate_path",
    "candidate_sha256",
    "queue_path",
    "primary_locator",
    "evidence",
    "coverage",
    "reproducible_snapshot",
    "retention_limitations",
    "writer_ref",
    "reviewer_ref",
    "reviewed_at",
    "disposition",
}
_EVIDENCE_FIELDS = {"path", "envelope_sha256", "content_sha256"}
_COVERAGE_FIELDS = {"status", "covered", "omissions"}
_DISPOSITIONS = {"publish-with-boundary", "defer", "reject"}
_LOCATOR_PREFIXES = ("http://", "https://")


class ReviewReceiptError(ValueError):
    """A malformed, unsafe, or publication-ineligible review receipt."""


_MODULES: dict[str, Any] = {}


def _load_tool(name: str):
    key = "review_receipt_" + name.replace("-", "_")
    if key in _MODULES:
        return _MODULES[key]
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).with_name(name + ".py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    _MODULES[key] = module
    spec.loader.exec_module(module)
    return module


def _guard():
    return _load_tool("kb-write-guard")


def _retain():
    return _load_tool("kb-retain-evidence")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(record: dict[str, Any]) -> bytes:
    """Return the one canonical byte representation used for receipt hashes."""
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _text(value: Any, field: str, *, nonempty: bool = True) -> str:
    if type(value) is not str:
        raise ReviewReceiptError(f"{field} must be text")
    if len(value) > MAX_FIELD_CHARS:
        raise ReviewReceiptError(f"{field} exceeds {MAX_FIELD_CHARS} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ReviewReceiptError(f"{field} contains a control character")
    if nonempty and not value.strip():
        raise ReviewReceiptError(f"{field} must be non-empty")
    return value


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewReceiptError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReviewReceiptError(f"non-finite JSON number is not allowed: {value}")


def _bounded_values(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ReviewReceiptError("review JSON nesting is excessive")
    if isinstance(value, str):
        if len(value) > MAX_FIELD_CHARS:
            raise ReviewReceiptError(
                f"review JSON string exceeds {MAX_FIELD_CHARS} characters"
            )
        return
    if isinstance(value, dict):
        if len(value) > MAX_LIST_ITEMS:
            raise ReviewReceiptError("review JSON object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReviewReceiptError("review JSON object keys must be text")
            _bounded_values(item, depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS:
            raise ReviewReceiptError("review JSON list has too many items")
        for item in value:
            _bounded_values(item, depth + 1)


def _parse_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_REVIEW_BYTES:
        raise ReviewReceiptError("review JSON exceeds 1 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewReceiptError("review JSON must be UTF-8") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except ReviewReceiptError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReviewReceiptError("invalid review JSON") from exc
    _bounded_values(parsed)
    if not isinstance(parsed, dict):
        raise ReviewReceiptError("review JSON root must be an object")
    return parsed


def _lexical_absolute(path: Path | str, root: Path) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = root / value
    if ".." in value.parts:
        raise ReviewReceiptError("path parent traversal is not allowed")
    return value


def _scoped(root: Path, path: Path | str) -> Path:
    try:
        return _retain().scoped(root, _lexical_absolute(path, root))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReviewReceiptError(str(exc)) from exc


def _relative(root: Path, path: Path | str) -> tuple[Path, Path]:
    candidate = _scoped(root, path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ReviewReceiptError("path escapes the KB root") from exc
    return candidate, relative


def _candidate_relative(value: Any) -> str:
    text = _text(value, "candidate_path")
    if "\\" in text or text.startswith("/") or text.startswith("knowledgebase/"):
        raise ReviewReceiptError("candidate_path must be a KB-relative path")
    path = Path(text)
    if ".." in path.parts or len(path.parts) != 4:
        raise ReviewReceiptError(
            "candidate_path must be topics/<topic>/sources/<note>.md"
        )
    if (
        path.parts[0] != "topics"
        or path.parts[2] != "sources"
        or path.suffix != ".md"
        or not path.parts[1]
        or not path.parts[3]
        or path.as_posix() != text
    ):
        raise ReviewReceiptError(
            "candidate_path must be topics/<topic>/sources/<note>.md"
        )
    return path.as_posix()


def _queue_relative(value: Any) -> str:
    text = _text(value, "queue_path")
    if "\\" in text or text.startswith("/") or text.startswith("knowledgebase/"):
        raise ReviewReceiptError("queue_path must be a KB-relative path")
    path = Path(text)
    if ".." in path.parts or len(path.parts) != 3:
        raise ReviewReceiptError(
            "queue_path must be topics/<topic>/reading-list.md"
        )
    if (
        path.parts[0] != "topics"
        or path.parts[2] != "reading-list.md"
        or path.as_posix() != text
    ):
        raise ReviewReceiptError(
            "queue_path must be topics/<topic>/reading-list.md"
        )
    return path.as_posix()


def _canonical_logical(root: Path, relative: str) -> Path:
    """Resolve a KB-relative logical path without following symlink components."""
    target = root / Path(*Path(relative).parts)
    # ``scoped`` checks every existing component, including a dangling final
    # symlink, while allowing the logical target not to exist yet.
    return _scoped(root, target)


def _review_path(root: Path, path: Path | str, *, allow_missing: bool = False) -> tuple[Path, str]:
    candidate, relative = _relative(root, path)
    if relative.parts[:-1] != STORE.parts:
        raise ReviewReceiptError("review path must be under _tools/reviews")
    if not re.fullmatch(r"[0-9a-f]{64}\.json", relative.name):
        raise ReviewReceiptError("review path is not a canonical receipt filename")
    if not allow_missing and not candidate.exists():
        raise ReviewReceiptError("review receipt does not exist")
    return candidate, relative.stem


def _stage_path(root: Path, path: Path | str) -> Path:
    candidate = _scoped(root, path)
    try:
        info = os.lstat(candidate)
    except FileNotFoundError as exc:
        raise ReviewReceiptError("staged review JSON does not exist") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ReviewReceiptError("staged review JSON must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ReviewReceiptError("staged review JSON must be a regular file")
    if info.st_nlink != 1:
        raise ReviewReceiptError("staged review JSON must be singly linked")
    return candidate


def _read_regular(root: Path, path: Path, *, limit: int) -> bytes:
    try:
        raw = _retain().read_regular(path, limit + 1)
    except (OSError, ValueError) as exc:
        raise ReviewReceiptError(str(exc)) from exc
    if len(raw) > limit:
        raise ReviewReceiptError(f"review artifact exceeds {limit} bytes")
    return raw


def _read_stage(root: Path, path: Path | str) -> bytes:
    candidate = _stage_path(root, path)
    return _read_regular(root, candidate, limit=MAX_REVIEW_BYTES)


def _validate_digest(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _DIGEST_RE.fullmatch(text):
        raise ReviewReceiptError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _validate_locator(value: Any, field: str = "primary_locator") -> str:
    text = _text(value, field)
    if text != text.strip() or any(char.isspace() for char in text):
        raise ReviewReceiptError(f"{field} must not contain whitespace")
    if text.startswith(_LOCATOR_PREFIXES):
        try:
            parts = urlsplit(text)
            hostname = parts.hostname
        except ValueError as exc:
            raise ReviewReceiptError(f"{field} is not a valid HTTP(S) URL") from exc
        try:
            _ = parts.port
        except ValueError as exc:
            raise ReviewReceiptError(f"{field} has an invalid port") from exc
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or not hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ReviewReceiptError(f"{field} is not a valid HTTP(S) URL")
        if parts.netloc.endswith(":"):
            raise ReviewReceiptError(f"{field} has an invalid port")
        # The review binds a source identity, not merely a fetchable URL.
        # ``kb-seen.normalize`` drops ports, so reject an endpoint that would
        # otherwise be indistinguishable from its default-port counterpart.
        try:
            _load_tool("kb-seen").validate_identity_url(text, field)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ReviewReceiptError(str(exc)) from exc
        return text
    if text.startswith("/") and text.casefold().endswith(".pdf"):
        if ".." in Path(text).parts:
            raise ReviewReceiptError(f"{field} must not contain parent traversal")
        return text
    raise ReviewReceiptError(
        f"{field} must be an exact HTTP(S) URL or absolute PDF path"
    )


def _validate_review_record(root: Path, record: Any) -> list[dict[str, Any]]:
    if not isinstance(record, dict) or set(record) != _TOP_LEVEL_FIELDS:
        raise ReviewReceiptError("invalid review receipt fields")
    if type(record["author"]) is not str or record["author"] != "kb-bot":
        raise ReviewReceiptError("review receipt author must be kb-bot")
    if type(record["version"]) is not int or record["version"] != 1:
        raise ReviewReceiptError("review receipt version must be 1")
    candidate_path = _candidate_relative(record["candidate_path"])
    _validate_digest(record["candidate_sha256"], "candidate_sha256")
    queue_path = _queue_relative(record["queue_path"])
    _validate_locator(record["primary_locator"])

    evidence = record["evidence"]
    if not isinstance(evidence, list):
        raise ReviewReceiptError("evidence must be a list")
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        raise ReviewReceiptError("too many retained evidence envelopes")
    evidence_records: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    for index, item in enumerate(evidence, 1):
        if not isinstance(item, dict) or set(item) != _EVIDENCE_FIELDS:
            raise ReviewReceiptError(f"evidence item {index} has invalid fields")
        path_text = _text(item["path"], f"evidence[{index}].path")
        if path_text.startswith("/") or "\\" in path_text:
            raise ReviewReceiptError(f"evidence[{index}].path must be KB-relative")
        path = Path(path_text)
        if (
            path.parts[:-1] != EVIDENCE_STORE.parts
            or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name)
            or path.as_posix() != path_text
        ):
            raise ReviewReceiptError(
                f"evidence[{index}].path must be _tools/evidence/<sha256>.json"
            )
        if path_text in seen_evidence:
            raise ReviewReceiptError("evidence paths must be distinct")
        seen_evidence.add(path_text)
        envelope_sha = _validate_digest(
            item["envelope_sha256"], f"evidence[{index}].envelope_sha256"
        )
        content_sha = _validate_digest(
            item["content_sha256"], f"evidence[{index}].content_sha256"
        )
        if path.stem != envelope_sha:
            raise ReviewReceiptError(
                f"evidence[{index}] envelope digest does not match its path"
            )
        evidence_path = _scoped(root, root / path)
        try:
            verified = _retain().verify(root, evidence_path)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ReviewReceiptError(
                f"evidence[{index}] failed retention verification: {exc}"
            ) from exc
        if verified["sha256"] != envelope_sha or verified["content_sha256"] != content_sha:
            raise ReviewReceiptError(f"evidence[{index}] digest metadata mismatch")
        evidence_limit = _retain().MAX_BYTES * 6 + 16384
        envelope_raw = _read_regular(root, evidence_path, limit=evidence_limit)
        envelope = _parse_evidence_envelope(envelope_raw)
        if digest(envelope_raw) != envelope_sha:
            raise ReviewReceiptError(f"evidence[{index}] envelope changed during verification")
        if envelope["content_sha256"] != content_sha:
            raise ReviewReceiptError(f"evidence[{index}] content digest mismatch")
        evidence_records.append(envelope)

    coverage = record["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != _COVERAGE_FIELDS:
        raise ReviewReceiptError("coverage must contain exactly status, covered, omissions")
    coverage_status = _text(coverage["status"], "coverage.status")
    if coverage_status not in {"complete", "partial"}:
        raise ReviewReceiptError("coverage.status must be complete or partial")
    covered = _coverage_list(coverage["covered"], "coverage.covered", required=True)
    omissions = _coverage_list(coverage["omissions"], "coverage.omissions", required=False)
    if record["disposition"] == "publish-with-boundary":
        if coverage_status == "partial" and not omissions:
            raise ReviewReceiptError(
                "publish-with-boundary partial coverage requires omissions"
            )
    _text(record["retention_limitations"], "retention_limitations")
    _text(record["writer_ref"], "writer_ref")
    _text(record["reviewer_ref"], "reviewer_ref")
    if record["writer_ref"].strip() == record["reviewer_ref"].strip():
        raise ReviewReceiptError("writer_ref and reviewer_ref must be distinct")
    reviewed_at = _text(record["reviewed_at"], "reviewed_at")
    try:
        parsed_date = dt.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewReceiptError("reviewed_at must be ISO-8601") from exc
    if parsed_date.tzinfo is None:
        raise ReviewReceiptError("reviewed_at must include a timezone")
    disposition = _text(record["disposition"], "disposition")
    if disposition not in _DISPOSITIONS:
        raise ReviewReceiptError("invalid review disposition")
    if type(record["reproducible_snapshot"]) is not bool:
        raise ReviewReceiptError("reproducible_snapshot must be boolean")
    if not evidence_records and record["reproducible_snapshot"]:
        raise ReviewReceiptError(
            "reproducible_snapshot requires retained evidence"
        )
    if record["reproducible_snapshot"]:
        if any(item["scope"] != "supplied-extraction" for item in evidence_records):
            raise ReviewReceiptError(
                "reproducible_snapshot requires supplied-extraction evidence, not excerpts"
            )
    # Keep local variables explicit: these fields are validated for exact path
    # shape above and then rebound in context by verify_review.
    del candidate_path, queue_path
    return evidence_records


def _coverage_list(value: Any, field: str, *, required: bool) -> list[str]:
    if not isinstance(value, list):
        raise ReviewReceiptError(f"{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise ReviewReceiptError(f"{field} has too many items")
    result = []
    for index, item in enumerate(value, 1):
        result.append(_text(item, f"{field}[{index}]", nonempty=True))
    if required and not result:
        raise ReviewReceiptError(f"{field} must be non-empty")
    return result


def _parse_evidence_envelope(raw: bytes) -> dict[str, Any]:
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReviewReceiptError("retained evidence envelope is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise ReviewReceiptError("retained evidence envelope must be an object")
    # retain.verify has already checked the exact envelope schema and canonical
    # bytes.  Reading these two provenance fields here avoids a second, subtly
    # different evidence implementation in this receipt tool.
    origin = envelope.get("origin")
    scope = envelope.get("scope")
    if type(origin) is not str or type(scope) is not str:
        raise ReviewReceiptError("retained evidence provenance is malformed")
    return envelope


def _candidate_context(root: Path, relative: str, text: str) -> dict[str, Any]:
    target = _canonical_logical(root, relative)
    try:
        metadata = _guard().strict_frontmatter(text, target)
    except (OSError, ValueError, TypeError) as exc:
        raise ReviewReceiptError(f"candidate frontmatter is invalid: {exc}") from exc
    if metadata.get("author") != "kb-bot" or metadata.get("type") != "source":
        raise ReviewReceiptError("candidate must be a kb-bot source note")
    if metadata.get("topic") != Path(relative).parts[1]:
        raise ReviewReceiptError("candidate topic disagrees with candidate_path")
    if metadata.get("ingest_status", "succeeded") == "failed":
        raise ReviewReceiptError("failed source candidates cannot be published")
    if metadata.get("status") == "deprecated":
        raise ReviewReceiptError("deprecated source candidates cannot be published")
    origin = metadata.get("origin")
    if type(origin) is not str or not origin.strip():
        raise ReviewReceiptError("candidate origin must be a non-empty string")
    _validate_locator(origin, "candidate origin")
    mirror = metadata.get("mirror", "")
    if mirror:
        if type(mirror) is not str:
            raise ReviewReceiptError("candidate mirror must be text")
        _validate_locator(mirror, "candidate mirror")
    return metadata


def _queue_context(root: Path, relative: str) -> Path:
    queue = root / Path(*Path(relative).parts)
    try:
        queue = _guard().canonical_queue_path(queue, root)
        _guard().guarded_text(queue, root)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ReviewReceiptError(f"queue path is unsafe: {exc}") from exc
    return queue


def _bind_context(
    root: Path,
    record: dict[str, Any],
    evidence_records: list[dict[str, Any]],
    *,
    candidate_path: str,
    candidate_text: str,
    queue_path: str,
    primary_locator: str,
    require_publish: bool,
) -> None:
    expected_candidate = _candidate_relative(candidate_path)
    expected_queue = _queue_relative(queue_path)
    expected_locator = _validate_locator(primary_locator)
    if record["candidate_path"] != expected_candidate:
        raise ReviewReceiptError("review candidate_path does not match the caller")
    if record["queue_path"] != expected_queue:
        raise ReviewReceiptError("review queue_path does not match the caller")
    if record["primary_locator"] != expected_locator:
        raise ReviewReceiptError("review primary_locator does not match the caller")
    try:
        candidate_digest = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ReviewReceiptError("candidate_text must be valid UTF-8 text") from exc
    if candidate_digest != record["candidate_sha256"]:
        raise ReviewReceiptError("candidate bytes do not match candidate_sha256")
    metadata = _candidate_context(root, expected_candidate, candidate_text)
    _queue_context(root, expected_queue)
    if Path(expected_candidate).parts[1] != Path(expected_queue).parts[1]:
        raise ReviewReceiptError("candidate and queue topics do not match")
    origin = metadata["origin"]
    allowed_origins = {origin, expected_locator}
    for index, envelope in enumerate(evidence_records, 1):
        if envelope["origin"] not in allowed_origins:
            raise ReviewReceiptError(
                f"evidence[{index}] origin is not candidate origin or primary locator"
            )
    if require_publish and record["disposition"] != "publish-with-boundary":
        raise ReviewReceiptError(
            f"review disposition {record['disposition']!r} does not permit publication"
        )


def _read_canonical(
    root: Path, review_path: Path | str
) -> tuple[dict[str, Any], str, Path, list[dict[str, Any]]]:
    canonical, stem = _review_path(root, review_path)
    raw = _read_regular(root, canonical, limit=MAX_REVIEW_BYTES)
    record = _parse_json(raw)
    evidence_records = _validate_review_record(root, record)
    canonical_bytes = encoded(record)
    if raw != canonical_bytes or digest(raw) != stem:
        raise ReviewReceiptError("review receipt digest/encoding mismatch")
    return record, stem, canonical, evidence_records


def verify_review(
    root: Path,
    review_path: Path | str,
    *,
    candidate_path: str,
    candidate_text: str,
    queue_path: str,
    primary_locator: str,
) -> str:
    """Internal adapter binding a receipt to caller-validated context.

    Call only after the shared source-completion gate validates the actual
    source bytes, source identity and queue locator. The publisher/checkoff
    owns locked row selection and approval checks. This function binds those
    supplied values, not independently re-reading/validating the selected row
    or comparing an on-disk source to the candidate snapshot.

    The return value is the receipt filename digest.  A successful return is a
    structural/provenance review gate, not a claim that the reviewed source is
    true or complete.
    """
    try:
        root = _guard().canonical_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ReviewReceiptError(f"unsafe KB root: {exc}") from exc
    if type(candidate_text) is not str:
        raise ReviewReceiptError("candidate_text must be text")
    record, stem, _, evidence_records = _read_canonical(root, review_path)
    _bind_context(
        root,
        record,
        evidence_records,
        candidate_path=candidate_path,
        candidate_text=candidate_text,
        queue_path=queue_path,
        primary_locator=primary_locator,
        require_publish=True,
    )
    return stem


def _validate_staged(root: Path, stage_path: Path | str) -> tuple[dict[str, Any], bytes]:
    raw = _read_stage(root, stage_path)
    record = _parse_json(raw)
    _validate_review_record(root, record)
    return record, encoded(record)


def retain(root: Path, stage_path: Path | str) -> dict[str, Any]:
    """Canonicalise and immutably retain one staged review JSON object."""
    try:
        root = _guard().canonical_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ReviewReceiptError(f"unsafe KB root: {exc}") from exc
    # Read-only preflight: no lock file or review directory is created until
    # the stage path, strict JSON, evidence, and schema have passed.
    record, canonical = _validate_staged(root, stage_path)
    receipt_digest = digest(canonical)
    target = _scoped(root, root / STORE / (receipt_digest + ".json"))

    with _guard().write_lock(root):
        # Re-read and revalidate under the shared lock to close the staged-file
        # replacement window.  A changed stage is an operator error, not a
        # reason to silently retain a different review than the preflight one.
        locked_record, locked_canonical = _validate_staged(root, stage_path)
        if locked_canonical != canonical:
            raise ReviewReceiptError("staged review JSON changed during validation")
        if locked_record != record:
            raise ReviewReceiptError("staged review JSON changed during validation")
        _ensure_review_store(root)
        if target.exists():
            _read_canonical(root, target)
            return {
                "status": "REVIEW_ALREADY_RETAINED",
                "path": target.relative_to(root).as_posix(),
                "sha256": receipt_digest,
            }
        try:
            _guard().atomic_create(
                target, canonical.decode("utf-8"), prefix=".review-create-"
            )
        except FileExistsError:
            # A non-cooperating race must not turn into an overwrite.  Accept
            # only the exact canonical artifact that won the race.
            _read_canonical(root, target)
            return {
                "status": "REVIEW_ALREADY_RETAINED",
                "path": target.relative_to(root).as_posix(),
                "sha256": receipt_digest,
            }
        _read_canonical(root, target)
        return {
            "status": "REVIEW_RETAINED",
            "path": target.relative_to(root).as_posix(),
            "sha256": receipt_digest,
        }


def _ensure_review_store(root: Path) -> Path:
    tools = root / "_tools"
    reviews = root / STORE
    for directory in (tools, reviews):
        scoped = _scoped(root, directory)
        try:
            info = os.lstat(scoped)
        except FileNotFoundError:
            scoped.mkdir()
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ReviewReceiptError(f"review store component is not a directory: {scoped}")
    return reviews


def verify_canonical(root: Path, review_path: Path | str) -> dict[str, Any]:
    """Verify a canonical artifact, binding on-disk context when available."""
    try:
        root = _guard().canonical_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ReviewReceiptError(f"unsafe KB root: {exc}") from exc
    record, stem, _, _ = _read_canonical(root, review_path)
    return {
        "status": "REVIEW_VERIFIED",
        "path": str(Path("_tools/reviews") / (stem + ".json")),
        "sha256": stem,
        "disposition": record["disposition"],
        "context": "artifact-only; verify_review required for candidate/queue binding",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path, help="owned staged JSON inside the KB")
    group.add_argument("--verify", type=Path, help="canonical receipt under _tools/reviews")
    args = parser.parse_args(argv)
    try:
        if args.input is not None:
            result = retain(args.kb_root, args.input)
        else:
            result = verify_canonical(args.kb_root, args.verify)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(json.dumps({"status": "STOP", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
