"""Read-only source evidence gate for queue completion. No fetches or writes.

A receipt checks structure, locator identity and a byte-bound review record,
not source truth or factual fidelity.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path


def load_tool(name: str):
    key = "kb_completion_" + name.replace("-", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def primary_locator(row: str) -> str | None:
    body = re.sub(r"^\s*-\s+\[[ xX!~?]\]\s*", "", row, count=1)
    match = re.match(r"(https?://[^\s]+|/[^\s]+\.pdf)(?:\s|$)", body)
    return match.group(1) if match else None


def completion_receipt(kb_root: Path, locator: str, note: str,
                       source_note: str | None = None, accept_warnings: str = "",
                       *, staged_text: str | None = None,
                       review_path: Path | str | None = None,
                       queue_path: str | None = None) -> str:
    write_guard = load_tool("kb-write-guard")
    try:
        root = write_guard.canonical_root(kb_root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f"unsafe KB root: {exc}") from exc
    if any("\n" in value or "\r" in value for value in (locator, note, source_note or "", accept_warnings)):
        raise ValueError("source receipt arguments must be single-line")
    refs = set(re.findall(r"\[\[(knowledgebase/topics/[^\]|#]+/sources/[^\]|#]+)(?:\|[^\]]*)?\]\]", note))
    if source_note is None:
        if len(refs) != 1:
            raise ValueError("source completion requires --source-note or one canonical source wikilink in --note")
        source_note = refs.pop()
    relative = source_note.removeprefix("knowledgebase/")
    if any(character in relative for character in "[]|#"):
        raise ValueError("source path contains reserved wikilink characters")
    if not relative.endswith(".md"):
        relative += ".md"
    parts = Path(relative).parts
    if len(parts) != 4 or parts[0] != "topics" or parts[2] != "sources" or ".." in parts:
        raise ValueError("source note must be topics/<topic>/sources/<note>.md")
    expected_ref = "knowledgebase/" + relative[:-3]
    if refs and refs != {expected_ref}:
        raise ValueError("completion annotation points to a different source note")
    candidate = root / relative
    path = candidate.resolve(strict=staged_text is None)
    path.relative_to(root)
    if path != candidate or (staged_text is None and not path.is_file()):
        raise ValueError("source note must be a regular non-symlink path")
    if staged_text is None:
        try:
            text = write_guard.regular_text(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                "source note must be a regular, singly-linked UTF-8 file"
            ) from exc
        raw = text.encode("utf-8")
    else:
        raw = staged_text.encode("utf-8")
        text = staged_text
    validator = load_tool("kb-validate-source-candidate")
    fm, _ = validator.split_frontmatter(text)
    if fm.get("author") != "kb-bot" or fm.get("type") != "source":
        raise ValueError("completion target is not a kb-bot source note")
    if fm.get("ingest_status", "succeeded") != "succeeded" or fm.get("status") == "deprecated":
        raise ValueError("failed or deprecated sources cannot establish completion")
    if fm.get("topic") != parts[1]:
        raise ValueError("source topic disagrees with its path")
    origin = str(fm.get("origin", ""))
    kb_seen = load_tool("kb-seen")
    try:
        # Source-index lookup deliberately projects only these identity fields
        # from historical notes.  New completion receipts must reject the same
        # ambiguity before an immutable source or queue mark is created.
        kb_seen._identity_frontmatter(text)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f"source identity is ambiguous: {exc}") from exc
    # Even local-file receipts validate the optional registry first.  A
    # malformed registry must not be bypassed by a non-URL completion path.
    kb_seen.load_alias_registry(root)
    if locator.startswith(("https://", "http://")):
        # Validate both identity endpoints before the normalized comparison.
        # This keeps a staged non-default-port origin from being accepted when
        # the queue locator happens to normalize to the same default-port key.
        kb_seen.validate_identity_url(locator, "queue locator")
        if origin.startswith(("https://", "http://")):
            kb_seen.validate_identity_url(origin, "source origin")
        if not origin.startswith(("https://", "http://")) or not kb_seen.url_matches_source(
            locator,
            relative,
            origin,
            root,
        ):
            raise ValueError("source origin does not match the queue item's primary URL")
    elif Path(origin).expanduser().resolve() != Path(locator).expanduser().resolve():
        raise ValueError("source origin does not match the queued local file")
    findings = validator.validate_text(text, path, root)
    blocking = [finding.code for finding in findings if finding.severity == "block"]
    warnings = [finding.code for finding in findings if finding.severity == "warn"]
    if blocking:
        raise ValueError("source validation blocked: " + ", ".join(sorted(set(blocking))))
    if warnings and not accept_warnings.strip():
        raise ValueError("source warnings require --accept-source-warnings with a reason: " + ", ".join(sorted(set(warnings))))
    if review_path is None:
        raise ValueError("source completion requires --review-receipt for the exact candidate and queue")
    review_digest = load_tool("kb-review-receipt").verify_review(
        root, review_path, candidate_path=relative, candidate_text=text,
        queue_path=queue_path or f"topics/{parts[1]}/reading-list.md",
        primary_locator=locator,
    )
    receipt = f"validated-source [[knowledgebase/{relative[:-3]}]] sha256:{hashlib.sha256(raw).hexdigest()}"
    if warnings:
        receipt += " warnings-accepted: " + accept_warnings.strip()
    receipt += f" — review-receipt sha256:{review_digest}"
    return receipt
