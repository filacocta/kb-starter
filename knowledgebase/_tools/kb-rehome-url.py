#!/usr/bin/env python3
"""Race-safe rehome of one reading-list row between topic queues.

The rehome verb is deliberately narrow: it moves one row from one canonical
topic reading list to another while holding the KB writer lock.  The source and
target files cannot be committed as one filesystem transaction, so a target
first commit is used.  If the source removal then fails, the result is reported
as a recoverable duplicate rather than being presented as a successful move.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator

LOCK_NAME = ".kb-write.lock"
TOPIC_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _queue_parser():
    """Load the shared pure queue parser despite its hyphenated filename."""
    key = "kb_rehome_queue_parser"
    module = sys.modules.get(key)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).with_name("kb-queue-parse.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kb-queue-parse.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


CANONICAL = list(_queue_parser().CANONICAL_SECTIONS)


def _guard_module():
    """Load the shared filesystem guard despite the hyphenated filename."""
    key = "kb_rehome_write_guard"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-write-guard.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load kb-write-guard.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def _single_line(name: str, value: str, *, allow_whitespace: bool = True) -> None:
    """Reject values that could create another markdown/YAML line."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be single-line")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    if not allow_whitespace and any(character.isspace() for character in value):
        raise ValueError(f"{name} must not contain whitespace")


def _canonical_root(kb_root: Path) -> Path:
    return _guard_module().canonical_root(kb_root)


def _canonical_lock(kb_root: Path, lock_path: Path) -> tuple[Path, Path]:
    root = _canonical_root(kb_root)
    expected = root / LOCK_NAME
    # Keep this small compatibility helper for callers/tests; the actual lock
    # open is delegated to the shared O_NOFOLLOW + flock implementation.
    candidate = lock_path.expanduser()
    absolute = candidate.absolute() if candidate.is_absolute() else (root / candidate).absolute()
    if absolute != expected:
        raise ValueError("invalid KB lock path")
    return root, expected


def _canonical_topic_path(root: Path, topic: str) -> Path:
    _single_line("topic", topic, allow_whitespace=False)
    if not TOPIC_SLUG_RE.fullmatch(topic):
        raise ValueError(f"invalid topic slug: {topic!r}")
    candidate = root / "topics" / topic / "reading-list.md"
    return _guard_module().canonical_queue_path(candidate, root)


def topic_reading_list(kb_root: Path, topic: str) -> Path:
    """Compatibility helper returning a topic's canonical queue path."""
    root = _canonical_root(kb_root)
    return _canonical_topic_path(root, topic)


def assert_kb_bot(path: Path) -> None:
    """Compatibility ownership assertion for callers of the old helper."""
    text = _guard_module().regular_text(path)
    if not text.startswith(("---\n", "---\r\n")):
        raise ValueError(f"{path} lacks YAML frontmatter")
    try:
        metadata = _guard_module().frontmatter_metadata(text.replace("\r\n", "\n"), path)
    except Exception as exc:
        raise ValueError(f"{path} has invalid YAML frontmatter") from exc
    if metadata.get("author") != "kb-bot":
        raise ValueError(f"{path} is not kb-bot authored; refusing to write")


def _guarded_text(path: Path, root: Path) -> str:
    """Read and validate a canonical topic reading list while locked."""
    return _guard_module().guarded_text(path, root)


@contextlib.contextmanager
def _write_lock(kb_root: Path, lock_path: Path) -> Iterator[Path]:
    root, expected = _canonical_lock(kb_root, lock_path)
    with _guard_module().write_lock(root, expected):
        yield root


def primary_locator(line: str) -> str | None:
    """Return the row's first locator token, never an annotation token."""
    return _queue_parser().primary_locator(line)


def _queue_text(lines: list[str]) -> str:
    """Join either ``splitlines()`` or ``splitlines(keepends=True)`` input."""
    if not lines:
        return ""
    if any(line.endswith(("\n", "\r")) for line in lines):
        return "".join(lines)
    return "\n".join(lines)


def _parsed_queue(lines: list[str]):
    """Parse a queue and fail closed for any structural ambiguity."""
    parsed = _queue_parser().parse_queue(_queue_text(lines))
    if parsed.errors:
        raise ValueError("; ".join(parsed.errors))
    return parsed


def _row_section(parsed, row) -> str:
    """Return the semantic H2 name for a row, including inactive headings."""
    if row.section is not None:
        return row.section
    for heading in reversed(parsed.headings):
        if heading.line < row.line:
            return heading.name
    return "(before first heading)"


def find_rows(lines: list[str], url: str, section: str | None = None) -> list[tuple[int, str]]:
    """Return rows whose *primary* locator equals ``url`` exactly."""
    parsed = _parsed_queue(lines)
    hits: list[tuple[int, str]] = []
    for row in parsed.rows:
        if row.locator != url:
            continue
        current = _row_section(parsed, row)
        if section is None or current == section:
            hits.append((row.line - 1, current))
    return hits


def section_insert_index(lines: list[str], section: str) -> int | None:
    return _queue_parser().section_insert_index(_queue_text(lines), section)


def append_note(line: str, note: str) -> str:
    if not note:
        return line
    _single_line("correction note", note)
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    if body.endswith("\r"):
        body = body[:-1]
    return f"{body.rstrip()} — {note}{newline}"


def _atomic_replace(path: Path, text: str, prefix: str) -> None:
    """Publish one existing regular file via a private same-directory temp."""
    return _guard_module().atomic_write(path, text, prefix=prefix)


def regenerate_dive_queue(kb_root: Path) -> None:
    """Best-effort refresh after the shared lock has been released."""
    script = Path(__file__).resolve().parent / "kb-derive-dive-queue.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--kb-root", str(kb_root), "--write"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as exc:  # pragma: no cover - best effort
        print(f"warning: dive-queue regen failed: {exc}", file=sys.stderr)


def rehome(
    kb_root: Path,
    from_topic: str,
    to_topic: str,
    url: str,
    lock_path: Path,
    from_section: str | None = None,
    to_section: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> tuple[bool, str, dict]:
    """Move one uniquely identified row and report any partial two-file state."""
    try:
        _single_line("URL", url, allow_whitespace=False)
        _single_line("correction note", note)
        _single_line("from section", from_section or "")
        _single_line("to section", to_section or "")
        root = _canonical_root(kb_root)
        source = _canonical_topic_path(root, from_topic)
        target = _canonical_topic_path(root, to_topic)
    except (OSError, ValueError) as exc:
        return False, str(exc), {}

    if from_topic == to_topic:
        return False, "--from-topic and --to-topic are the same; use kb-promote.py for same-topic section moves", {}
    if from_section is not None and from_section not in CANONICAL:
        return False, f"--from-section must be a canonical section {CANONICAL}, got {from_section!r}", {}
    if to_section is not None and to_section not in CANONICAL:
        return False, f"--to-section must be a canonical section {CANONICAL}, got {to_section!r}", {}

    try:
        with _write_lock(root, lock_path):
            source_text = _guarded_text(source, root)
            target_text = _guarded_text(target, root)
            source_lines = source_text.splitlines(keepends=True)
            target_lines = target_text.splitlines(keepends=True)

            hits = find_rows(source_lines, url, from_section)
            if not hits:
                section_msg = f" under '## {from_section}'" if from_section else ""
                return False, f"no line with exact primary locator {url!r}{section_msg} in {source}", {}
            if len(hits) > 1:
                return False, f"ambiguous: {len(hits)} source lines exactly match {url!r} in {source}", {}

            target_hits = find_rows(target_lines, url)
            if target_hits:
                detail = "; ".join(f"line {i + 1} in ## {section}" for i, section in target_hits)
                return False, f"target already contains primary locator {url!r}: {detail}", {}

            idx, detected_section = hits[0]
            destination_section = to_section or detected_section
            if destination_section not in CANONICAL:
                return False, f"source row is under non-canonical section '## {detected_section}'", {}
            insert_at = section_insert_index(target_lines, destination_section)
            if insert_at is None:
                return False, f"target section '## {destination_section}' not found in {target}", {}

            moved = append_note(source_lines[idx], note)
            if not moved.endswith("\n"):
                moved += "\n"
            info = {
                "status": "DRY_RUN" if dry_run else "PLANNED",
                "from_topic": from_topic,
                "to_topic": to_topic,
                "from_section": detected_section,
                "to_section": destination_section,
                "source_line": idx + 1,
                "moved_text": _queue_parser().CHECKBOX_RE.sub("", moved, count=1).strip(),
                "source_written": False,
                "target_written": False,
            }
            if dry_run:
                return True, (
                    f"[dry-run] would rehome line {idx + 1} from {from_topic} "
                    f"## {detected_section} to {to_topic} ## {destination_section}"
                ), info

            del source_lines[idx]
            insert_at = section_insert_index(target_lines, destination_section)
            if insert_at is None:  # defensive; target was checked above
                return False, f"target section '## {destination_section}' disappeared", info
            target_lines.insert(insert_at, moved)

            # There is no cross-file atomic commit.  Publish the target first so
            # an interruption leaves a duplicate that the dedupe verb can repair,
            # never a silently lost row.
            target_expected = "".join(target_lines)
            source_expected = "".join(source_lines)
            try:
                _atomic_replace(target, target_expected, ".kb-rehome-target-")
                info["target_written"] = True
                _atomic_replace(source, source_expected, ".kb-rehome-source-")
                info["source_written"] = True
            except Exception as exc:
                # An atomic helper can theoretically raise after os.replace
                # (for example while syncing a directory).  Re-read both
                # checked files so the status reflects bytes actually present,
                # not merely which call returned.
                for key, path, expected in (
                    ("target_written", target, target_expected),
                    ("source_written", source, source_expected),
                ):
                    if not info[key]:
                        try:
                            info[key] = _guarded_text(path, root) == expected
                        except Exception:
                            pass
                if info["target_written"] and info["source_written"]:
                    info["status"] = "COMMITTED_WITH_ERROR"
                else:
                    info["status"] = "PARTIAL" if info["target_written"] or info["source_written"] else "FAILED"
                info["partial"] = bool(info["target_written"] != info["source_written"])
                if info["status"] == "COMMITTED_WITH_ERROR":
                    info["recovery"] = "both files contain the planned bytes; verify the ledger and dashboard before retrying"
                if info["target_written"] and not info["source_written"]:
                    info["recovery"] = "target and source both contain the row; inspect and reconcile the two queues before retrying"
                elif info["source_written"] and not info["target_written"]:
                    info["recovery"] = "source no longer contains the row; inspect target before retrying"
                return False, f"{info['status']}: rehome write incomplete: {exc}", info

            info["status"] = "COMPLETE"
            info["target_line"] = insert_at + 1
            return True, (
                f"rehomed line from {from_topic} ## {detected_section} "
                f"to {to_topic} ## {destination_section}"
            ), info
    except (OSError, ValueError) as exc:
        return False, str(exc), {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Race-safe cross-topic reading-list rehome.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--from-topic", required=True)
    parser.add_argument("--to-topic", required=True)
    parser.add_argument("--url", required=True, help="exact primary locator identifying the source row")
    parser.add_argument("--from-section", choices=CANONICAL, default=None)
    parser.add_argument("--to-section", choices=CANONICAL, default=None)
    parser.add_argument("--note", default="", help="optional single-line correction note")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit a JSON result object on stdout")
    args = parser.parse_args(argv)

    # Keep lexical ``..``/symlink components visible to the shared root guard.
    kb_root = Path(args.kb_root).expanduser()
    ok, msg, info = rehome(
        kb_root=kb_root,
        from_topic=args.from_topic,
        to_topic=args.to_topic,
        url=args.url,
        lock_path=kb_root / LOCK_NAME,
        from_section=args.from_section,
        to_section=args.to_section,
        note=args.note,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps({"ok": ok, "message": msg, **info}))
    else:
        print(msg, file=sys.stdout if ok else sys.stderr)
    if ok and not args.dry_run:
        regenerate_dive_queue(kb_root)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
