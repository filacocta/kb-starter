#!/usr/bin/env python3
"""Validate and publish one approved staged source; then verify queue completion.

No fetching, model execution, git operations, or timer changes. A source may be
written while its queue remains pending; retry with identical bytes to resume.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml


def tool(name: str):
    key = "commit_" + name.replace("-", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def require_single_line(name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be single-line")


def canonical_queue(root: Path, queue: Path) -> tuple[Path, str]:
    """Return one canonical topic reading-list path and its topic slug."""
    candidate = queue if queue.is_absolute() else root / queue
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("queue file must be inside the KB root") from exc
    if len(relative.parts) != 3 or relative.parts[0] != "topics" or relative.parts[2] != "reading-list.md":
        raise ValueError("commit source requires topics/<topic>/reading-list.md")
    if candidate != resolved:
        raise ValueError("queue file must be a canonical non-symlink path")
    return resolved, relative.parts[1]


def canonical_source(root: Path, candidate_path: str) -> tuple[Path, str, str]:
    """Return target path, vault-relative source path, and topic slug."""
    logical = candidate_path.removeprefix("knowledgebase/")
    relative = Path(logical if logical.endswith(".md") else logical + ".md")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("candidate path must be a relative topic source path")
    if len(relative.parts) != 4 or relative.parts[0] != "topics" or relative.parts[2] != "sources":
        raise ValueError("candidate path must be topics/<topic>/sources/<note>.md")
    target = root / relative
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("candidate path must be inside the KB root") from exc
    if resolved != target:
        raise ValueError("candidate path must be a canonical non-symlink path")
    return target, relative.as_posix(), relative.parts[1]


def _approved_row(
    text: str,
    url: str,
    mode: str,
    kb_root: Path | None = None,
    *,
    identity_index: dict[str, str] | None = None,
):
    """Select one approved row and return the index used for this pass.

    A caller may supply an already validated in-memory index so publication
    checks in one lock phase share one authoritative read.  The default still
    loads the index after the queue's approval structure has been checked,
    preserving the standalone helper's existing gate ordering.
    """
    kb_seen = tool("kb-seen")
    queue_parse = tool("kb-queue-parse")
    parsed = queue_parse.parse_queue(text)
    # Publication is approval-sensitive: malformed or duplicate canonical
    # approval headings must not turn a Markdown example or an ambiguous queue
    # into an ingestion lane.  URL normalisation/aliases remain dedup/source
    # identity concerns after this exact row selection.
    parsed.require_approval_safe()
    if kb_root is not None and identity_index is None:
        # Validate the authoritative identity registry once per pass.  The
        # returned index is intentionally not used to select a queue row:
        # selectors remain exact primary-locator tokens.
        identity_index = kb_seen.load_index(kb_root)
    matches = [
        (row.raw, row.section, row.locator)
        for row in parsed.rows
        if row.mark.lower() in {" ", "x"}
        and row.locator is not None
        and row.locator == url
    ]
    if len(matches) != 1:
        raise ValueError("approved queue requires exactly one matching URL/PDF row")
    row, section, locator = matches[0]
    allowed = {"Auto-approved"} if mode == "headless" else {"Approved", "Auto-approved"}
    if section not in allowed:
        raise ValueError("queue item is not in an approved ingestion section")
    return row, locator, identity_index


def approved_row(
    text: str,
    url: str,
    mode: str,
    kb_root: Path | None = None,
):
    """Return one approved queue row while retaining standalone compatibility."""
    row, locator, _ = _approved_row(text, url, mode, kb_root)
    return row, locator


def origin_key(origin: str):
    origin = origin.strip()
    if not origin:
        return None
    if origin.startswith(("http://", "https://")):
        return ("url", tool("kb-seen").normalize(origin))
    return ("local", str(Path(origin).expanduser().resolve()))


def duplicate_successful_origin(
    root: Path,
    origins: list[str],
    target: Path,
    *,
    identity_index: dict[str, str] | None = None,
) -> Path | None:
    """Find another non-failed source sharing an origin or transport mirror."""
    kb_seen = tool("kb-seen")
    # Validate the optional registry even when the candidate itself is a local
    # file.  A malformed registry must never be silently bypassed by a
    # publication path that happens not to need an alias lookup.
    kb_seen.load_alias_registry(root)
    keys = {key for value in origins if (key := origin_key(value)) is not None}
    if not keys:
        return None
    validator = tool("kb-validate-source-candidate")
    target_relative = None
    try:
        target_relative = target.relative_to(root).as_posix()
    except ValueError:
        pass

    # Use the shared authoritative source iterator for URL identities.  It
    # reads each candidate through the regular, singly-linked file guard and
    # fails closed on symlink/hardlink source notes before any fallback scan.
    try:
        for normalized, relative in kb_seen.iter_origins(root):
            if ("url", normalized) in keys and relative != target_relative:
                return root / Path(*relative.split("/"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"unsafe source index: {exc}") from exc

    # A staged source whose URL is an explicitly governed legacy alias must be
    # treated as the existing source that owns that alias.  This closes the
    # duplicate-publish gap without rewriting either immutable source note.
    for value in origins:
        if not value.startswith(("http://", "https://")):
            continue
        matched = kb_seen.source_for_url(value, root, identity_index)
        if matched is not None and matched != target_relative:
            return root / matched

    # Local-file origins are not part of kb-seen's URL index.  Preserve that
    # duplicate check, but use the same guarded reader instead of
    # Path.read_text(), which could follow a source-note symlink or hardlink.
    local_keys = {key for key in keys if key[0] == "local"}
    if not local_keys:
        return None
    write_guard = tool("kb-write-guard")
    for path in sorted(root.glob("topics/*/sources/*.md")):
        try:
            relative = path.relative_to(root).as_posix()
            if relative == target_relative:
                continue
            text = write_guard.regular_text(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"unsafe source index note {path}: {exc}") from exc
        try:
            frontmatter, _ = validator.split_frontmatter(text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"unsafe source index note {path}: {exc}") from exc
        if frontmatter.get("type") != "source":
            continue
        if frontmatter.get("ingest_status", "succeeded") != "succeeded":
            continue
        existing_keys = {
            existing_key
            for field in ("origin", "mirror")
            if (existing_key := origin_key(str(frontmatter.get(field, "")))) is not None
        }
        if keys & existing_keys:
            return path
    return None


def verify_existing_target(target: Path, staged_text: str) -> bool:
    """Allow only an identical, owned source target to be resumed."""
    if target.is_symlink():
        raise ValueError("source target must not be a symlink")
    if not target.exists():
        return False
    if not target.is_file():
        raise ValueError("source target exists but is not a regular file")
    existing = tool("kb-write-guard").regular_text(target)
    if existing != staged_text:
        raise ValueError("existing source differs; immutable note will not be overwritten")
    validator = tool("kb-validate-source-candidate")
    frontmatter, _ = validator.split_frontmatter(existing)
    if frontmatter.get("author") != "kb-bot" or frontmatter.get("type") != "source":
        raise ValueError("existing source target is not an owned source note")
    return True


def validate_staged(
    root: Path,
    target: Path,
    relative: str,
    locator: str,
    staged_text: str,
    accept_warnings: str,
    review_path: Path | str | None = None,
    *,
    identity_index: dict[str, str] | None = None,
) -> str:
    validator = tool("kb-validate-source-candidate")
    frontmatter, _ = validator.split_frontmatter(staged_text)
    duplicate = duplicate_successful_origin(
        root,
        [str(frontmatter.get(field, "")) for field in ("origin", "mirror")],
        target,
        identity_index=identity_index,
    )
    if duplicate is not None:
        raise ValueError(
            "source origin already exists in successful source: "
            + duplicate.relative_to(root).as_posix()
        )
    return tool("kb-source-completion").completion_receipt(
        root,
        locator,
        "",
        relative,
        accept_warnings,
        staged_text=staged_text,
        review_path=review_path,
    )


def publish_source(target: Path, staged_text: str, root: Path) -> None:
    parent = target.parent.resolve(strict=True)
    parent.relative_to(root)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=".kb-source-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(staged_text)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and refuses to overwrite a race-created target.
        os.link(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def partial_result(digest: str, message: str, *, error: str | None = None) -> dict:
    result = {
        "status": "SOURCE_WRITTEN_QUEUE_PENDING",
        "source_written": True,
        "queue_completed": False,
        "source_sha256": digest,
        "message": message,
        "resume": "Retry with the identical staged bytes after inspecting queue state.",
    }
    if error is not None:
        result["error"] = error
    return result


def commit_source(root, staged, relative, queue, url, *, mode="interactive", accept_warnings="", review_path=None):
    root = tool("kb-write-guard").canonical_root(root)
    for name, value in (
        ("candidate path", relative),
        ("queue path", str(queue)),
        ("URL", url),
        ("warning acceptance reason", accept_warnings),
    ):
        require_single_line(name, value)

    queue, queue_topic = canonical_queue(root, queue)
    target, relative, target_topic = canonical_source(root, relative)
    if queue_topic != target_topic:
        raise ValueError("queue topic and source target topic must match")
    try:
        # Staged drafts may live outside the KB, but the input itself must be
        # one checked regular file.  This prevents a symlink or hardlink from
        # silently changing the bytes that become an immutable source note.
        staged_text = tool("kb-write-guard").regular_text(staged)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "staged text must be a regular, singly-linked UTF-8 file"
        ) from exc
    digest = hashlib.sha256(staged_text.encode()).hexdigest()
    lock_path = root / ".kb-write.lock"
    if lock_path.is_symlink():
        raise ValueError("KB lock must not be a symlink")

    checkoff = tool("kb-checkoff")
    # Read-only preflight happens before lock creation, so invalid staged text
    # cannot leave even a lock-file write behind.
    queue_text = checkoff.guarded_text(queue, root)
    row, locator, prelock_index = _approved_row(queue_text, url, mode, root)
    verify_existing_target(target, staged_text)
    validate_staged(
        root,
        target,
        relative,
        locator,
        staged_text,
        accept_warnings,
        review_path,
        identity_index=prelock_index,
    )

    source_written = False
    with tool("kb-write-guard").write_lock(root, lock_path):
        queue_text = checkoff.guarded_text(queue, root)
        row, locator, locked_index = _approved_row(queue_text, url, mode, root)
        source_exists = verify_existing_target(target, staged_text)
        receipt = validate_staged(
            root,
            target,
            relative,
            locator,
            staged_text,
            accept_warnings,
            review_path,
            identity_index=locked_index,
        )
        if not source_exists:
            if re.match(r"^\s*-\s+\[[xX]\]", row):
                raise ValueError("queue is already complete but the target source is missing")
            publish_source(target, staged_text, root)
        source_written = True
        if re.match(r"^\s*-\s+\[[xX]\]", row):
            if receipt not in row:
                raise ValueError("completed row lacks matching validated-source receipt; manual reconciliation required")
            return {"status": "SOURCE_AND_QUEUE_COMPLETE", "resumed": True, "source_sha256": digest}

    note = f"ingested {dt.date.today().isoformat()}"
    required_sections = {"Auto-approved"} if mode == "headless" else {"Approved", "Auto-approved"}
    try:
        ok, message = checkoff.checkoff(
            queue,
            locator,
            "x",
            note,
            lock_path,
            kb_root=root,
            source_note=relative,
            accept_source_warnings=accept_warnings,
            required_sections=required_sections,
            review_path=review_path,
        )
    except Exception as exc:  # A published source must report resumable partial state.
        return partial_result(digest, "queue checkoff raised after source publication", error=str(exc))
    if ok:
        checkoff.regenerate_dive_queue(root)
        return {
            "status": "SOURCE_AND_QUEUE_COMPLETE",
            "source_written": source_written,
            "queue_completed": True,
            "source_sha256": digest,
            "message": message,
        }
    return partial_result(digest, message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged-text", type=Path, required=True)
    parser.add_argument("--candidate-path", required=True)
    parser.add_argument("--queue-file", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", choices=["interactive", "headless"], default="interactive")
    parser.add_argument("--accept-source-warnings", default="")
    parser.add_argument("--review-receipt", type=Path, help="Canonical immutable review receipt for these exact draft bytes")
    args = parser.parse_args(argv)
    try:
        result = commit_source(
            args.kb_root,
            args.staged_text,
            args.candidate_path,
            args.queue_file,
            args.url,
            mode=args.mode,
            accept_warnings=args.accept_source_warnings,
            review_path=args.review_receipt,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        result = {
            "status": "STOP",
            "error": str(exc),
            "resume": "Inspect target and queue state; never assume rollback or overwrite a source.",
        }
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "SOURCE_AND_QUEUE_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
