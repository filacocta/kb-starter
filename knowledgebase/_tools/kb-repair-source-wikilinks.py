#!/usr/bin/env python3
"""Repair narrowly-scoped wikilink target defects in source notes.

This is the source preservation contract exception for an immutable source note's navigation-only
repairs.  The target is checked under the shared KB write lock, the source is
re-read while that lock is held, and the shared mode-preserving atomic writer
is used for an apply.  Dry-run remains the default.

Only ordinary wikilinks in the Markdown body are eligible.  YAML frontmatter,
fenced code, inline code, blockquotes, and image/media embeds are preserved
verbatim.  The helper deliberately is not a general Markdown editor.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


WIKILINK_RE = re.compile(r"(!?)\[\[([^\]|#]+)((?:[#|][^\]]*)?)\]\]")
FENCE_RE = re.compile(
    r"^[ ]{0,3}(?P<run>" + re.escape(chr(96)) + r"{3,}|~{3,})(?P<rest>[^\r\n]*)"
)
LOCK_NAME = ".kb-write.lock"


def _load_write_guard():
    """Load the shared guard without requiring a package install."""
    key = "_kb_repair_source_wikilinks_write_guard"
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def fail(message: str, code: int = 2) -> None:
    print(f"kb-repair-source-wikilinks: {message}", file=sys.stderr)
    raise SystemExit(code)


def _frontmatter_parts(text: str) -> tuple[str, str]:
    """Return the exact frontmatter prefix and body, without rewriting either."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    close = end + len("\n---\n")
    return text[:close], text[close:]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Compatibility helper returning parsed metadata and the exact body.

    The strict parser from the shared guard is authoritative for an actual
    source/target file.  This wrapper is retained for callers that used the
    original helper's small parsing function.
    """
    prefix, body = _frontmatter_parts(text)
    if not prefix:
        return {}, body
    try:
        metadata = _write_guard.frontmatter_metadata(text, Path("<frontmatter>"))
    except _write_guard.WriteGuardError:
        return {}, body
    return metadata, body


def kb_relative(path: Path, kb_root: Path) -> str:
    """Return a lexical KB-relative path without following source symlinks."""
    try:
        return path.relative_to(kb_root).as_posix()
    except ValueError:
        fail(f"path is outside KB root: {path}")


def _lexical_relative(path: Path, kb_root: Path) -> Path:
    try:
        relative = path.relative_to(kb_root)
    except ValueError as exc:
        raise _write_guard.WriteGuardError("path escapes the KB root") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise _write_guard.WriteGuardError("parent traversal is not allowed")
    return relative


def source_path_from_arg(raw: str, kb_root: Path) -> Path:
    """Resolve and constrain ``--file`` to one topic source-note path."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = kb_root / candidate
    relative = _lexical_relative(candidate, kb_root)
    if (
        len(relative.parts) != 4
        or relative.parts[0] != "topics"
        or not relative.parts[1]
        or relative.parts[2] != "sources"
        or not relative.parts[3]
        or not relative.parts[3].endswith(".md")
    ):
        raise _write_guard.WriteGuardError(
            "source file must be topics/<topic>/sources/<note>.md"
        )
    return candidate


def normalize_target(raw: str) -> str:
    """Strip wikilink delimiters, display labels, and anchors from a target."""
    value = raw.strip()
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2]
    if "|" in value:
        value = value.split("|", 1)[0]
    if "#" in value:
        value = value.split("#", 1)[0]
    return value.strip()


def parse_replacement(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        fail(f"--replace must be OLD=NEW, got: {raw}")
    old, new = raw.split("=", 1)
    old = normalize_target(old)
    new = normalize_target(new)
    if not old or not new:
        fail(f"--replace must include both OLD and NEW: {raw}")
    if old.startswith(("http://", "https://", "_media/", "knowledgebase/_media/")):
        fail(f"old target is not an ordinary KB-note wikilink: {old}")
    if not new.startswith("knowledgebase/"):
        fail(f"replacement must be vault-rooted with knowledgebase/: {new}")
    return old, new


def note_path_from_target(target: str, kb_root: Path) -> Path:
    """Resolve a full vault-rooted Markdown target without following links."""
    normalized = normalize_target(target)
    if not normalized.startswith("knowledgebase/"):
        fail(f"replacement must be vault-rooted with knowledgebase/: {target}")
    relative_raw = normalized.removeprefix("knowledgebase/")
    if not relative_raw or relative_raw.startswith("/"):
        fail(f"empty or absolute replacement target: {target}")
    relative = Path(relative_raw)
    if any(part in {"", ".", ".."} for part in relative.parts):
        fail(f"replacement target contains unsafe path components: {target}")
    if relative.parts[0] == "_media":
        fail(f"replacement target is not a Markdown note: {target}")
    if relative.suffix and relative.suffix != ".md":
        fail(f"replacement target is not a Markdown note: {target}")
    if not relative.suffix:
        relative = relative.with_suffix(".md")
    path = kb_root / relative
    _lexical_relative(path, kb_root)
    return path


def _validate_identity(
    text: str,
    path: Path,
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    """Require unambiguous, full YAML identity for source and target notes."""
    try:
        metadata = _write_guard.frontmatter_metadata(text, path)
    except _write_guard.WriteGuardError as exc:
        fail(f"invalid YAML frontmatter in {path}: {exc}")
    author = metadata.get("author")
    note_type = metadata.get("type")
    if not isinstance(author, str) or author != "kb-bot":
        fail(f"authorship guard failed for {path}")
    if not isinstance(note_type, str) or not note_type:
        fail(f"note has no unambiguous YAML type: {path}")
    if expected_type is not None and note_type != expected_type:
        fail(f"source note must have type: source: {path}")
    return metadata


def _replace_line_outside_inline_code(
    line: str,
    replacements: dict[str, str],
    changes: list[dict[str, Any]],
    *,
    initial_code_ticks: int = 0,
) -> tuple[str, int]:
    """Replace ordinary wikilinks while preserving inline-code spans."""
    output: list[str] = []
    index = 0
    code_ticks = initial_code_ticks
    while index < len(line):
        character = line[index]
        if character == "`":
            end = index + 1
            while end < len(line) and line[end] == "`":
                end += 1
            run = end - index
            output.append(line[index:end])
            if code_ticks == 0:
                code_ticks = run
            elif run == code_ticks:
                code_ticks = 0
            index = end
            continue
        if code_ticks:
            output.append(character)
            index += 1
            continue

        match = WIKILINK_RE.match(line, index)
        if match is None:
            output.append(character)
            index += 1
            continue

        embed, target, suffix = match.groups()
        key = normalize_target(target)
        replacement = replacements.get(key)
        # Image/media embeds are never navigation substitutions, even if a
        # caller supplies a matching target.  Ordinary labels and anchors are
        # retained in ``suffix`` byte-for-byte.
        if embed or replacement is None:
            output.append(match.group(0))
        else:
            output.append(f"[[{replacement}{suffix}]]")
            changes.append({"from": target, "to": replacement, "suffix": suffix})
        index = match.end()
    return "".join(output), code_ticks


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    match = FENCE_RE.match(line)
    if match is None:
        return None
    run = match.group("run")
    return run[0], len(run), match.group("rest")


def _fence_closes(line: str, character: str, minimum_length: int) -> bool:
    marker = _fence_marker(line)
    if marker is None:
        return False
    found_character, found_length, rest = marker
    return (
        found_character == character
        and found_length >= minimum_length
        and not rest.strip()
    )


def repair_body(body: str, replacements: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """Repair only eligible body links, with a conservative Markdown boundary."""
    changes: list[dict[str, Any]] = []
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    inline_code_ticks = 0
    blockquote_continuation = False
    for line in body.splitlines(keepends=True):
        if fence is not None:
            lines.append(line)
            if _fence_closes(line, fence[0], fence[1]):
                fence = None
            continue
        if line.startswith("\t") or line.startswith("    "):
            lines.append(line)
            continue
        if blockquote_continuation:
            lines.append(line)
            if not line.strip():
                blockquote_continuation = False
            continue
        if line.lstrip().startswith(">"):
            lines.append(line)
            blockquote_continuation = True
            continue
        if inline_code_ticks:
            repaired, inline_code_ticks = _replace_line_outside_inline_code(
                line, replacements, changes, initial_code_ticks=inline_code_ticks
            )
            lines.append(repaired)
            continue
        marker = _fence_marker(line)
        if marker is not None:
            lines.append(line)
            fence = (marker[0], marker[1])
            continue
        repaired, inline_code_ticks = _replace_line_outside_inline_code(
            line, replacements, changes
        )
        lines.append(repaired)
    return "".join(lines), changes


def repair_text(text: str, replacements: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """Preserve frontmatter and apply ``repair_body`` to the remaining text."""
    prefix, body = _frontmatter_parts(text)
    repaired_body, changes = repair_body(body, replacements)
    return prefix + repaired_body, changes


def atomic_write(path: Path, text: str) -> None:
    """Compatibility wrapper around the shared mode-preserving atomic writer."""
    _write_guard.atomic_write(path, text, prefix=".kb-repair-source-wikilinks-")


def _validate_target(path: Path) -> None:
    """Require a checked regular, singly-linked, kb-bot Markdown note target."""
    target_text = _write_guard.regular_text(path)
    _validate_identity(target_text, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mechanically repair wikilink targets inside a source note."
    )
    parser.add_argument("--file", required=True, help="Source note path, KB-relative or absolute.")
    parser.add_argument(
        "--replace",
        action="append",
        required=True,
        metavar="OLD=NEW",
        help="Replace wikilink target OLD with NEW. NEW must point to an existing KB Markdown note.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the repair. Default is dry-run.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        kb_root = _write_guard.canonical_root(repo_root())
        source_path = source_path_from_arg(args.file, kb_root)
        requested = [parse_replacement(item) for item in args.replace]

        # The lock covers all reads and the possible replacement.  This keeps
        # dry-run evidence coherent and ensures apply re-reads the source just
        # before the shared atomic writer runs.
        with _write_guard.write_lock(kb_root, kb_root / LOCK_NAME):
            source_text = _write_guard.regular_text(source_path)
            _validate_identity(source_text, source_path, expected_type="source")

            replacements: dict[str, str] = {}
            for old, new in requested:
                if old in replacements and replacements[old] != new:
                    fail(f"ambiguous replacement requested for old target: {old}")
                target_path = note_path_from_target(new, kb_root)
                _validate_target(target_path)
                replacements[old] = new

            updated, changes = repair_text(source_text, replacements)
            if not changes:
                # A frontmatter-only, code-only, quote-only, or media-only
                # match is intentionally rejected rather than edited.
                fail("no eligible body wikilinks found", code=1)
            if updated == source_text:
                fail("no text change produced", code=1)

            result = {
                "file": kb_relative(source_path, kb_root),
                "applied": bool(args.apply),
                "changes": changes,
            }
            if args.apply:
                atomic_write(source_path, updated)

        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            action = "applied" if args.apply else "dry-run"
            print(f"{action}: {result['file']}")
            for change in changes:
                print(f"- {change['from']} -> {change['to']}{change['suffix']}")
        return 0
    except _write_guard.WriteGuardError as exc:
        fail(f"unsafe KB path or lock: {exc}")
    except (OSError, UnicodeError) as exc:
        fail(f"I/O failure: {exc}")
    return 2  # pragma: no cover - fail() always exits


if __name__ == "__main__":
    raise SystemExit(main())
