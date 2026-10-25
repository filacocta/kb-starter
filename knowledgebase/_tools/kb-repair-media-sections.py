#!/usr/bin/env python3
"""Add missing OCR/Vision placeholder sections to captured image media notes.

Dry-run is the default.  Apply mode only edits existing, singly-linked,
``author: kb-bot`` media-description notes whose captured media is an image.
The helper does not fetch media, run OCR/vision, touch source notes, or repair
frontmatter.  It only adds missing ``## OCR`` and ``## Vision Description``
sections with an explicit ``*Pending.*`` placeholder.

All reads and possible writes are made under the shared KB write guard.  The
guard's mode-preserving atomic writer is used for apply mode; a note is read
again immediately before each write so a direct path change cannot cause a
stale plan to overwrite a newer note.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


LOCK_NAME = ".kb-write.lock"
WRITE_PREFIX = ".kb-repair-media-sections-"
SECTION_NAMES = ("OCR", "Vision Description")
LATE_SECTION_RE = re.compile(
    r"^##[ \t]+(?:Transcript|Interpretation Notes|See Also)[ \t]*$",
    re.MULTILINE,
)


def _load_write_guard() -> Any:
    """Load the shared guard without requiring a package install."""
    key = "_kb_repair_media_sections_write_guard"
    module = sys.modules.get(key)
    if module is not None:
        return module
    script = Path(__file__).with_name("kb-write-guard.py")
    spec = importlib.util.spec_from_file_location(key, script)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-write-guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


_write_guard = _load_write_guard()


def kb_root() -> Path:
    """Return the repository root used by the default CLI invocation."""
    return Path(__file__).resolve().parents[1]


def _frontmatter_parts(text: str) -> tuple[str, str]:
    """Return the exact frontmatter prefix and body without rewriting either."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    close = end + len("\n---\n")
    return text[:close], text[close:]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str, str]:
    """Compatibility parser returning metadata plus exact prefix/body.

    Actual repairs use :func:`_parse_note`, whose strict shared-guard parser
    rejects malformed and duplicate YAML.  This small wrapper preserves the
    original helper's public shape for offline callers while refusing to
    pretend malformed frontmatter is valid.
    """
    prefix, body = _frontmatter_parts(text)
    if not prefix:
        return {}, "", text
    try:
        metadata = _write_guard.frontmatter_metadata(text, Path("<frontmatter>"))
    except _write_guard.WriteGuardError:
        return {}, "", text
    if not isinstance(metadata, dict):
        return {}, "", text
    return metadata, prefix, body


def _parse_note(text: str, path: Path) -> tuple[dict[str, Any], str, str]:
    """Strictly parse an existing note while preserving its exact bytes."""
    prefix, body = _frontmatter_parts(text)
    if not prefix:
        raise _write_guard.WriteGuardError(
            f"media-description note lacks YAML frontmatter: {path}"
        )
    metadata = _write_guard.frontmatter_metadata(text, path)
    if not isinstance(metadata, dict):
        raise _write_guard.WriteGuardError(
            f"media-description note has invalid YAML frontmatter: {path}"
        )
    return metadata, prefix, body


def has_heading(body: str, heading: str) -> bool:
    """Return whether an exact level-two Markdown heading already exists."""
    return (
        re.search(
            rf"^##[ \t]+{re.escape(heading)}[ \t]*$", body, re.MULTILINE
        )
        is not None
    )


def _blank_line_boundary(prefix: str) -> str:
    """Add only the separators needed before a newly inserted section.

    ``prefix`` is returned byte-for-byte and is never stripped.  This matters
    for hand-authored trailing spaces and for notes whose existing body ends
    without a final newline.
    """
    if not prefix:
        return prefix
    if prefix.endswith("\n\n"):
        return prefix
    if prefix.endswith("\n"):
        return prefix + "\n"
    return prefix + "\n\n"


def insert_before_late_sections(body: str, section: str) -> str:
    """Insert one pending section without rewriting existing body content."""
    block = f"## {section}\n\n*Pending.*\n\n"
    match = LATE_SECTION_RE.search(body)
    if match:
        return _blank_line_boundary(body[: match.start()]) + block + body[match.start() :]
    return _blank_line_boundary(body) + block


def update_modified(frontmatter: str, today: str) -> str:
    """Legacy compatibility helper; repairs intentionally do not call it.

    The repair's authority is limited to adding body placeholders.  Keeping
    this function avoids breaking callers that imported the old helper, while
    apply mode preserves frontmatter exactly and therefore does not change
    lifecycle metadata or status fields.
    """
    if re.search(r"^modified:[ \t]*", frontmatter, re.MULTILINE):
        return re.sub(
            r"^modified:[ \t]*.*$",
            f"modified: {today}",
            frontmatter,
            flags=re.MULTILINE,
        )
    return frontmatter.replace("\n---\n", f"\nmodified: {today}\n---\n", 1)


def needs_repair(fm: dict[str, Any]) -> bool:
    """Check the narrow eligibility contract without changing metadata.

    Historical media notes may predate the explicit ``media_kind`` field.  As
    in the original helper, an absent kind is treated as the legacy image
    default for eligibility only; apply mode never writes that inferred value.
    If the field is present, it must explicitly be the scalar ``image``.
    """
    return (
        isinstance(fm, dict)
        and fm.get("author") == "kb-bot"
        and fm.get("type") == "media-description"
        and fm.get("media_status") == "captured"
        and fm.get("media_kind", "image") == "image"
    )


def _absolute_lexical(path: Path | str, root: Path) -> Path:
    """Make an absolute path while rejecting lexical parent traversal."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if ".." in candidate.parts:
        raise _write_guard.WriteGuardError("parent traversal is not allowed")
    return candidate


def canonical_media_note_path(path: Path | str, root: Path) -> Path:
    """Validate one canonical ``topics/<topic>/notes/media/*.md`` path."""
    candidate = _absolute_lexical(path, root)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise _write_guard.WriteGuardError("path escapes the KB root") from exc
    if (
        len(relative.parts) != 5
        or relative.parts[0] != "topics"
        or not relative.parts[1]
        or relative.parts[2:4] != ("notes", "media")
        or not relative.parts[4]
        or not relative.parts[4].endswith(".md")
    ):
        raise _write_guard.WriteGuardError(
            "media note must be topics/<topic>/notes/media/<note>.md"
        )
    return candidate


def _validate_topic(raw: str | None) -> str | None:
    """Validate the optional topic selector as one lexical path component."""
    if raw is None:
        return None
    candidate = Path(raw).expanduser()
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 1
        or candidate.parts[0] in {"", ".", ".."}
        or "\\" in raw
    ):
        raise _write_guard.WriteGuardError(
            "topic must be one path component without traversal"
        )
    return candidate.parts[0]


def _infer_root(path: Path | str) -> Path:
    """Infer a KB root for direct compatibility calls to ``repair_note``."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if len(candidate.parents) < 5:
        raise _write_guard.WriteGuardError(
            "cannot infer KB root from media-note path"
        )
    # root/topics/<topic>/notes/media/<note>.md
    return candidate.parents[4]


def _inspect_note(path: Path | str, root: Path) -> dict[str, Any] | None:
    """Read and plan one note; private text is used only by the writer."""
    candidate = canonical_media_note_path(path, root)
    text = _write_guard.regular_text(candidate)
    fm, frontmatter, body = _parse_note(text, candidate)
    if not needs_repair(fm):
        return None

    added: list[str] = []
    new_body = body
    for section in SECTION_NAMES:
        if not has_heading(new_body, section):
            new_body = insert_before_late_sections(new_body, section)
            added.append(section)
    if not added:
        return None

    return {
        "path": str(candidate),
        "added": added,
        "_text": frontmatter + new_body,
        "_original": text,
    }


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def repair_note(
    path: Path,
    apply: bool,
    *,
    kb_root: Path | None = None,
    lock_held: bool = False,
) -> dict[str, Any] | None:
    """Inspect one note, optionally applying under the shared lock.

    ``lock_held`` is used by :func:`main` after it acquires one lock for the
    whole scan.  Direct callers retain the original function's behaviour and
    get a lock automatically, while all apply writes still use the shared
    atomic writer.
    """
    root = _write_guard.canonical_root(kb_root or _infer_root(path))
    candidate = canonical_media_note_path(path, root)

    def run() -> dict[str, Any] | None:
        item = _inspect_note(candidate, root)
        if item is None or not apply:
            return _public_item(item) if item is not None else None

        # Re-read and re-plan immediately before writing.  A direct writer
        # that changed content under the path cannot leave this helper with a
        # stale body plan; an identity change is rejected by regular_text and
        # atomic_write.
        refreshed = _inspect_note(candidate, root)
        if refreshed is None:
            return None
        _write_guard.atomic_write(
            candidate,
            refreshed["_text"],
            prefix=WRITE_PREFIX,
        )
        return _public_item(refreshed)

    if lock_held:
        return run()
    with _write_guard.write_lock(root, nonblocking=True):
        return run()


def _scope_paths(root: Path, topic: str | None) -> list[Path]:
    """List only lexical media-note paths in the requested scope."""
    if topic is not None:
        scope = root / "topics" / topic / "notes" / "media"
        # Reject a symlinked scope before asking pathlib to enumerate it.  A
        # missing optional topic simply has no candidates.
        try:
            _write_guard._walk_no_symlinks(scope, allow_final_missing=True)
        except FileNotFoundError:
            return []
        return sorted(scope.glob("*.md"))

    topics = root / "topics"
    try:
        _write_guard._walk_no_symlinks(topics, allow_final_missing=True)
    except FileNotFoundError:
        return []
    return sorted(root.glob("topics/*/notes/media/*.md"))


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", default=str(kb_root()))
    parser.add_argument("--topic", help="Optional topic slug.")
    parser.add_argument("--apply", action="store_true", help="Write repairs. Default is dry-run.")
    return parser


def _error_result(
    applied: bool,
    repaired: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    exc: BaseException,
) -> int:
    result = {
        "applied": applied,
        "repaired": repaired,
        "skipped": skipped,
        "error": str(exc),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    try:
        # Keep the lexical argument intact so canonical_root can reject a
        # symlinked KB root rather than silently resolving through it.
        root = _write_guard.canonical_root(Path(args.kb_root).expanduser())
        topic = _validate_topic(args.topic)

        # A non-blocking shared lock fails closed if another KB writer is
        # active; it also keeps a CLI repair invocation from hanging behind a
        # writer that may have crashed or become unavailable.
        with _write_guard.write_lock(root, root / LOCK_NAME, nonblocking=True):
            plans: list[tuple[Path, dict[str, Any]]] = []
            for path in _scope_paths(root, topic):
                try:
                    item = _inspect_note(path, root)
                except (OSError, UnicodeError, _write_guard.WriteGuardError) as exc:
                    # Unsafe or malformed existing notes are never written.
                    # Continue scanning independent candidates and report the
                    # exact skipped path for review.
                    skipped.append(
                        {
                            "path": _relative(path, root),
                            "reason": str(exc),
                        }
                    )
                    continue
                if item is None:
                    continue

                plans.append((path, item))

            # Do not partially apply a batch when one existing candidate has
            # ambiguous ownership/YAML or an unsafe path.  Dry-run remains a
            # useful inventory and reports these entries in ``skipped``;
            # apply mode fails closed before its first atomic replacement.
            if args.apply and skipped:
                raise _write_guard.WriteGuardError(
                    f"apply refused because {len(skipped)} candidate(s) were unsafe or malformed"
                )

            for path, item in plans:
                if args.apply:
                    # The first inspection produced the dry-run evidence;
                    # this second strict read is the apply re-read.
                    refreshed = _inspect_note(path, root)
                    if refreshed is None:
                        continue
                    _write_guard.atomic_write(
                        path,
                        refreshed["_text"],
                        prefix=WRITE_PREFIX,
                    )
                    item = refreshed
                public = _public_item(item)
                public["path"] = _relative(path, root)
                repaired.append(public)

        print(
            json.dumps(
                {"applied": bool(args.apply), "repaired": repaired, "skipped": skipped},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, UnicodeError, _write_guard.WriteGuardError) as exc:
        return _error_result(bool(args.apply), repaired, skipped, exc)


if __name__ == "__main__":
    raise SystemExit(main())
