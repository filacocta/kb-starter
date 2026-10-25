#!/usr/bin/env python3
"""Race-safe removal of one redundant duplicate reading-list row.

This is the narrow DEDUPE verb.  It removes a uniquely identified row only when
the same primary locator remains in another canonical section of the same file;
it can therefore never remove the last copy.  The optional ``--fold-note``
operation preserves the dropped row's trailing annotation on a uniquely chosen
keeper row.
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


def _queue_parser():
    """Load the shared pure queue parser despite its hyphenated filename."""
    key = "kb_dedupe_queue_parser"
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
    key = "kb_dedupe_write_guard"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("kb-write-guard.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load kb-write-guard.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def _single_line(name: str, value: str, *, allow_whitespace: bool = True) -> None:
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
    candidate = lock_path.expanduser()
    absolute = candidate.absolute() if candidate.is_absolute() else (root / candidate).absolute()
    if absolute != expected:
        raise ValueError("invalid KB lock path")
    return root, expected


def _guarded_text(path: Path, root: Path) -> str:
    """Validate one canonical mutable queue and return its current bytes."""
    return _guard_module().guarded_text(path, root)


@contextlib.contextmanager
def _write_lock(kb_root: Path, lock_path: Path) -> Iterator[Path]:
    root, expected = _canonical_lock(kb_root, lock_path)
    with _guard_module().write_lock(root, expected):
        yield root


def primary_locator(line: str) -> str | None:
    """Return only the first locator token after the checkbox."""
    return _queue_parser().primary_locator(line)


def _queue_text(lines: list[str]) -> str:
    """Join either ``splitlines()`` or ``splitlines(keepends=True)`` input."""
    if not lines:
        return ""
    if any(line.endswith(("\n", "\r")) for line in lines):
        return "".join(lines)
    return "\n".join(lines)


def rows_by_section(lines: list[str], url: str) -> dict[str, list[int]]:
    """Map section names to rows whose primary locator equals ``url``."""
    out: dict[str, list[int]] = {}
    parsed = _queue_parser().parse_queue(_queue_text(lines))
    if parsed.errors:
        raise ValueError("; ".join(parsed.errors))
    for row in parsed.rows:
        if row.locator != url or row.section is None:
            continue
        out.setdefault(row.section, []).append(row.line - 1)
    return out


def note_after_url(line: str, url: str) -> str:
    """Return the annotation after the exact primary locator token."""
    locator = primary_locator(line)
    if locator != url:
        return ""
    body = _queue_parser().CHECKBOX_RE.sub("", line.rstrip("\r\n"), count=1).lstrip()
    tail = body[len(url):].strip()
    tail = re.sub(r"^[—–-][ \t]*", "", tail)
    return tail.strip()


def _atomic_replace(path: Path, text: str) -> None:
    """Replace an existing queue with a unique same-directory temp file."""
    return _guard_module().atomic_write(path, text, prefix=".kb-dedupe-")


def regenerate_dive_queue(kb_root: Path) -> None:
    script = Path(__file__).resolve().parent / "kb-derive-dive-queue.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--kb-root", str(kb_root), "--write"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as exc:  # pragma: no cover - best effort
        print(f"warning: dive-queue regen failed: {exc}", file=sys.stderr)


def dedupe(
    file_path: Path,
    url: str,
    drop_section: str,
    into_section: str | None,
    fold_note: bool,
    lock_path: Path,
    kb_root: Path,
    dry_run: bool = False,
) -> tuple[bool, str, dict]:
    try:
        _single_line("URL", url, allow_whitespace=False)
        _single_line("drop section", drop_section)
        _single_line("into section", into_section or "")
        root = _canonical_root(kb_root)
        # Do not resolve the target before entering the lock: the guarded read
        # below re-checks the canonical path after writers have been excluded.
        target_path = file_path.expanduser()
        if not target_path.is_absolute():
            target_path = root / target_path
    except (OSError, ValueError) as exc:
        return False, str(exc), {}

    if drop_section not in CANONICAL:
        return False, f"--drop must be a canonical section {CANONICAL}, got {drop_section!r}", {}
    if into_section is not None and into_section not in CANONICAL:
        return False, f"--into must be a canonical section {CANONICAL}, got {into_section!r}", {}
    if into_section == drop_section:
        return False, "--into and --drop are the same section", {}

    try:
        with _write_lock(root, lock_path):
            # Preserve the caller's lexical path until the shared guard sees
            # it; resolving first would hide a ``..`` component or symlink.
            resolved = _guard_module().canonical_queue_path(target_path, root)
            text = _guarded_text(resolved, root)
            lines = text.splitlines(keepends=True)
            sect = rows_by_section(lines, url)

            drop_hits = sect.get(drop_section, [])
            if not drop_hits:
                return False, f"no line with exact primary locator {url!r} under '## {drop_section}'", {}
            if len(drop_hits) > 1:
                return False, f"ambiguous: {len(drop_hits)} lines exactly match {url!r} under '## {drop_section}'", {}

            # SAFETY: refuse unless the URL also exists in another section.
            other_sections = [s for s in sect if s != drop_section]
            if not other_sections:
                return False, (
                    f"refusing: {url!r} appears only under '## {drop_section}' — "
                    "not a cross-section duplicate (this verb never removes the last copy)"
                ), {}

            drop_idx = drop_hits[0]
            dropped_line = lines[drop_idx]
            fold_target_idx: int | None = None
            if into_section is not None:
                into_hits = sect.get(into_section, [])
                if not into_hits:
                    return False, f"--into '## {into_section}' has no line with exact primary locator {url!r} to fold into", {}
                if len(into_hits) > 1:
                    return False, f"ambiguous --into: {len(into_hits)} lines exactly match {url!r} under '## {into_section}'", {}
                fold_target_idx = into_hits[0]

            info = {
                "status": "DRY_RUN" if dry_run else "PLANNED",
                "file": str(resolved),
                "url": url,
                "drop_section": drop_section,
                "drop_line": drop_idx + 1,
                "also_in": sorted(other_sections),
                "into_section": into_section,
                "folded": False,
                "written": False,
            }
            if dry_run:
                return True, (
                    f"[dry-run] would remove line {drop_idx + 1} under '## {drop_section}' "
                    f"(kept in: {', '.join(sorted(other_sections))})"
                ), info

            # Fold before deleting so a later write failure leaves the original
            # file untouched and provenance intact.
            if fold_note and fold_target_idx is not None:
                note = note_after_url(dropped_line, url)
                if note:
                    keeper = lines[fold_target_idx]
                    newline = "\n" if keeper.endswith("\n") else ""
                    body = keeper[:-1] if newline else keeper
                    if body.endswith("\r"):
                        body = body[:-1]
                    if note not in body:
                        lines[fold_target_idx] = body.rstrip() + f" — (deduped from {drop_section}: {note})" + newline
                        info["folded"] = True

            del lines[drop_idx]
            try:
                _atomic_replace(resolved, "".join(lines))
            except Exception as exc:
                info["status"] = "FAILED"
                return False, f"FAILED: dedupe write did not commit: {exc}", info
            info["status"] = "COMPLETE"
            info["written"] = True
            return True, (
                f"removed duplicate under '## {drop_section}' in {resolved.name} "
                f"(kept in: {', '.join(sorted(other_sections))})"
            ), info
    except (OSError, ValueError) as exc:
        return False, str(exc), {}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Race-safe removal of a redundant duplicate reading-list row.")
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--file", required=True, help="queue file (relative to kb-root or absolute)")
    p.add_argument("--url", required=True, help="exact primary locator identifying the duplicate row")
    p.add_argument("--drop", dest="drop_section", required=True, help="section whose duplicate row to remove (canonical)")
    p.add_argument("--into", dest="into_section", help="keeper section to fold the dropped note into (canonical)")
    p.add_argument("--fold-note", action="store_true", help="append the dropped row's note to the --into keeper row")
    p.add_argument("--dry-run", action="store_true", help="report the planned removal without writing")
    p.add_argument("--json", action="store_true", help="emit a JSON result object on stdout")
    args = p.parse_args(argv)

    # Keep lexical ``..``/symlink components visible to the shared root guard.
    kb_root = Path(args.kb_root).expanduser()
    fp = Path(args.file)
    if not fp.is_absolute():
        fp = kb_root / fp
    ok, msg, info = dedupe(
        fp, args.url, args.drop_section, args.into_section, args.fold_note,
        kb_root / LOCK_NAME, kb_root, dry_run=args.dry_run,
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
