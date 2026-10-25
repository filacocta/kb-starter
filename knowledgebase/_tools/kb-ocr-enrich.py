#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""OCR + Vision Description enrichment for KB media-description notes.

For each media-description note in scope that has:
  - author: kb-bot
  - media_status: captured
  - A `*Pending.*`-marked ## OCR or ## Vision Description section

Run the requested enrichment stage:
  - --ocr-only   : requested OCR engine on local_path -> ## OCR + ## OCR Raw
  - --vision-only: retired compatibility mode (fails closed)
  - --both       : retired compatibility mode (fails closed; also the default)

Idempotent: skips a section if its content does not begin with `*Pending.*`.
Authorship and filesystem guards refuse unsafe or malformed targets. Well-formed
notes outside the captured-image lane are reported as skips. This is intended for
`notes/media/*.md` files (kb-bot-authored, mutable per media capture contract enrichment rule).

Usage:
  # Pilot: 3 notes, OCR only
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --ocr-engine tesseract --limit 3

  # Full topic, OCR only with an explicitly selected engine
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --ocr-engine tesseract

  # Vision-only is retired; inspect the retained image and delegate interpretation
  # to the active native KB worker (no CLI simulation).
  kb-ocr-enrich.py --topic earthquake-prediction --vision-only

  # Single note, OCR only
  kb-ocr-enrich.py --note topics/earthquake-prediction/notes/media/foo.md --ocr-only --ocr-engine tesseract

  # Dry run (no writes)
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --dry-run
"""

from __future__ import annotations

import argparse
from collections import namedtuple
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def _load_write_guard() -> Any:
    """Load the shared KB filesystem guard without requiring a package install."""
    key = "_kb_ocr_enrich_write_guard"
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

LOCK_NAME = ".kb-write.lock"
WRITE_PREFIX = ".kb-ocr-enrich-"
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class OCRGuardError(_write_guard.WriteGuardError):
    """A note/media target failed the narrow OCR commit contract."""


class StaleSnapshotError(OCRGuardError):
    """The note or media changed while OCR was running."""


class IneligibleNoteError(ValueError):
    """A well-formed note is outside OCR's captured-image eligibility lane."""


OCRAttempt = namedtuple("OCRAttempt", ("engine", "raw_text", "cleaned_text"))
MAX_OCR_RESULT_BYTES = 2 * 1024 * 1024
MAX_OCR_ATTEMPTS = 256
MAX_MISTRAL_RESPONSE_BYTES = 8 * 1024 * 1024
OCRResult = namedtuple(
    "OCRResult", ("engine", "raw_text", "cleaned_text", "attempts"),
    defaults=((),),
)
OCRResult.__doc__ = """OCR output with an auditable raw channel and a derived display channel.

``raw_text`` is the engine's decoded text output before the renderer's cleanup
rules.  ``cleaned_text`` is intentionally derived and may omit lines that look
like prompt echo or model commentary.  ``attempts`` keeps every engine-labelled
attempt.  The historical ``auto`` attempt-combination helper remains available
to direct callers for compatibility, but is retired from the active CLI so
engine selection is always explicit.
"""


def _single_ocr_result(engine: str, raw_text: str, cleaned_text: str) -> OCRResult:
    attempt = OCRAttempt(
        engine=engine,
        raw_text=raw_text,
        cleaned_text=cleaned_text,
    )
    return OCRResult(
        engine=engine,
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        attempts=(attempt,),
    )


def _validate_result_size(result: OCRResult) -> None:
    """Stop oversized enrichment rather than silently truncating evidence."""
    attempts = result.attempts or (OCRAttempt(result.engine, result.raw_text, result.cleaned_text),)
    if len(attempts) > MAX_OCR_ATTEMPTS:
        raise OCRGuardError("OCR result exceeds the attempt/page limit")
    size = len(result.cleaned_text.encode("utf-8"))
    for attempt in attempts:
        size += len(attempt.raw_text.encode("utf-8"))
        size += len(attempt.cleaned_text.encode("utf-8"))
        if size > MAX_OCR_RESULT_BYTES:
            raise OCRGuardError("OCR raw/derived result exceeds the 2 MiB retention limit; note unchanged")


def _coerce_legacy_result(engine: str, value: OCRResult | str) -> OCRResult:
    """Adapt an older monkeypatched/string engine result to the new channel."""
    if isinstance(value, OCRResult):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{engine} OCR runner must return str or OCRResult")
    return _single_ocr_result(engine, value, value)


def _decode_subprocess_output(value: bytes | str, *, stream: str) -> str:
    """Decode engine output without universal-newline rewriting.

    Subprocesses are read as bytes so CR/LF bytes survive the decode exactly;
    the stored raw channel is the resulting UTF-8 string, not a binary stdout
    archive.  Synthetic test doubles may provide ``str`` directly.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError(f"OCR subprocess {stream} must be bytes or str")

RETIRED_VISION_MESSAGE = (
    "vision interpretation is retired; inspect the retained image, then delegate interpretation "
    "to the active native KB worker (no CLI simulation)."
)


class RetiredVisionRouteError(RuntimeError):
    """Raised when the retired provider-backed vision route is requested."""


def reject_retired_vision_route(route: str) -> None:
    raise RetiredVisionRouteError(f"{route} is retired; {RETIRED_VISION_MESSAGE}")


PENDING_RE = re.compile(r"^\s*\*?Pending\b", re.MULTILINE | re.IGNORECASE)


StatIdentity = namedtuple(
    "StatIdentity", ("dev", "ino", "size", "mtime_ns", "ctime_ns")
)
NoteSnapshot = namedtuple(
    "NoteSnapshot",
    (
        "note_path", "image_path", "note_text", "note_identity",
        "image_identity", "frontmatter", "body", "metadata",
    ),
)


def _identity(info: os.stat_result) -> StatIdentity:
    return StatIdentity(
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    )


def _absolute_lexical(path: Path | str, root: Path) -> Path:
    """Make an absolute path without resolving aliases or parent traversal."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if ".." in candidate.parts:
        raise OCRGuardError("parent traversal is not allowed")
    return candidate


def canonical_note_path(path: Path | str, root: Path) -> Path:
    """Validate one existing canonical ``topics/<topic>/notes/media/*.md`` path."""
    raw = str(path)
    if "\\" in raw or "//" in raw or any(part == "." for part in raw.split("/")):
        raise OCRGuardError("media note path contains a non-canonical alias")
    candidate = _absolute_lexical(path, root)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise OCRGuardError("media note path escapes the KB root") from exc
    if (
        len(relative.parts) != 5
        or relative.parts[0] != "topics"
        or not relative.parts[1]
        or relative.parts[1] in {".", ".."}
        or relative.parts[2:4] != ("notes", "media")
        or not relative.parts[4]
        or relative.parts[4] in {".", ".."}
        or not relative.parts[4].endswith(".md")
        or relative.parts[4] == ".md"
        or "\\" in relative.parts[1]
    ):
        raise OCRGuardError(
            "media note must be topics/<topic>/notes/media/<note>.md"
        )
    # This lexical walk is intentionally not a resolve().  It rejects aliases
    # in every component while retaining the exact path for the final check.
    try:
        _write_guard._walk_no_symlinks(candidate)
    except (OSError, _write_guard.WriteGuardError) as exc:
        if isinstance(exc, OCRGuardError):
            raise
        raise OCRGuardError(f"unsafe media note path: {candidate}: {exc}") from exc
    return candidate


def _validate_topic(raw: str | None) -> str | None:
    """Validate an optional topic selector as one lexical path component."""
    if raw is None:
        return None
    candidate = Path(raw).expanduser()
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 1
        or candidate.parts[0] in {"", ".", ".."}
        or "\\" in raw
    ):
        raise OCRGuardError("topic must be one path component without traversal")
    return candidate.parts[0]


def _frontmatter_parts(text: str, path: Path) -> tuple[dict[str, Any], str, str]:
    """Parse strict YAML while preserving the exact frontmatter and body."""
    if not text.startswith("---\n"):
        raise OCRGuardError(f"media-description note lacks YAML frontmatter: {path}")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise OCRGuardError(f"media-description note lacks YAML frontmatter: {path}")
    prefix = text[: end + 5]
    body = text[end + 5 :]
    try:
        metadata = _write_guard.strict_frontmatter(text, path)
    except _write_guard.WriteGuardError as exc:
        raise OCRGuardError(str(exc)) from exc
    if not isinstance(metadata, dict):
        raise OCRGuardError(f"media-description note has invalid YAML frontmatter: {path}")
    return metadata, prefix, body


def _checked_regular_identity(path: Path, *, binary: bool = False) -> tuple[StatIdentity, bytes | None]:
    """Open a non-symlink, singly-linked regular file and return its identity.

    Binary media is deliberately not copied or decoded here.  The descriptor
    checks establish the cooperative-writer contract; the identity is checked
    again after OCR before any note commit.
    """
    try:
        descriptor, before = _write_guard._open_regular(
            path,
            flags=os.O_RDONLY | _O_NONBLOCK,
        )
    except (OSError, _write_guard.WriteGuardError) as exc:
        raise OCRGuardError(f"unsafe regular file: {path}: {exc}") from exc
    data: bytes | None = None
    try:
        if binary:
            after = os.fstat(descriptor)
        else:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        before_identity = _identity(before)
        after_identity = _identity(after)
        if before_identity != after_identity:
            raise StaleSnapshotError(f"file changed while reading: {path}")
    except OCRGuardError:
        raise
    except OSError as exc:
        raise OCRGuardError(f"could not read checked file: {path}: {exc}") from exc
    finally:
        os.close(descriptor)
    return before_identity, data


def _checked_text(path: Path) -> tuple[str, StatIdentity]:
    """Read exact UTF-8 text and retain the descriptor-derived identity."""
    identity, data = _checked_regular_identity(path)
    assert data is not None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OCRGuardError(f"media-description note is not valid UTF-8: {path}") from exc
    return text, identity


def _image_path_from_metadata(metadata: dict[str, Any], root: Path, note_path: Path) -> Path:
    """Resolve only the two documented vault-relative media path forms."""
    raw = metadata.get("local_path")
    if not isinstance(raw, str) or not raw.strip():
        raise OCRGuardError(f"media note has no valid local_path: {note_path}")
    raw_parts = raw.split("/")
    if (
        raw != raw.strip()
        or "\\" in raw
        or "//" in raw
        or any(part == "." for part in raw_parts)
    ):
        raise OCRGuardError(f"local_path is not a canonical vault-relative path: {raw!r}")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise OCRGuardError(f"local_path must not be absolute or use traversal: {raw!r}")
    if relative.parts[0] == "_media":
        image_path = root / relative
    elif relative.parts[0] == "knowledgebase" and len(relative.parts) > 1:
        # ``knowledgebase/`` is the vault-relative spelling used in notes.  In
        # a fixture or alternate checkout the filesystem root may have another
        # basename; strip only this documented marker, never resolve aliases.
        image_path = root / Path(*relative.parts[1:])
    else:
        raise OCRGuardError(
            "local_path must begin with _media/ or knowledgebase/_media/"
        )
    media_root = root / "_media"
    try:
        image_path.relative_to(media_root)
    except ValueError as exc:
        raise OCRGuardError(f"local_path escapes the KB _media directory: {raw!r}") from exc
    return image_path


def _validate_metadata(metadata: dict[str, Any], note_path: Path, root: Path) -> Path:
    """Apply the narrow OCR eligibility contract before selecting an engine."""
    expected = {
        "author": "kb-bot",
        "type": "media-description",
        "media_status": "captured",
        "media_kind": "image",
    }
    for field, value in expected.items():
        actual = metadata.get(field)
        if not isinstance(actual, str) or actual != value:
            raise IneligibleNoteError(
                f"media note is not an eligible captured image: {field}={actual!r}; expected {value!r}"
            )
    return _image_path_from_metadata(metadata, root, note_path)


def _snapshot_note(note_path: Path, root: Path) -> NoteSnapshot:
    """Read a canonical note and its image under the caller-held lock."""
    note = canonical_note_path(note_path, root)
    note_text, note_identity = _checked_text(note)
    metadata, frontmatter, body = _frontmatter_parts(note_text, note)
    image_path = _validate_metadata(metadata, note, root)
    image_identity, _ = _checked_regular_identity(image_path, binary=True)
    return NoteSnapshot(
        note_path=note,
        image_path=image_path,
        note_text=note_text,
        note_identity=note_identity,
        image_identity=image_identity,
        frontmatter=frontmatter,
        body=body,
        metadata=metadata,
    )


def _same_snapshot(before: NoteSnapshot, after: NoteSnapshot) -> bool:
    return (
        before.note_path == after.note_path
        and before.image_path == after.image_path
        and before.note_text == after.note_text
        and before.note_identity == after.note_identity
        and before.image_identity == after.image_identity
    )


# ----- frontmatter parsing -----


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """Compatibility parser using the shared duplicate-key-safe YAML loader."""
    try:
        metadata, _prefix, body = _frontmatter_parts(text, Path("<frontmatter>"))
    except OCRGuardError:
        return None
    return metadata, body


# ----- section replacement -----


def find_section(body: str, heading: str) -> tuple[int, int] | None:
    """Return ``(content_start, content_end)`` for a top-level ``##`` section.

    OCR output is user/model text and can itself contain Markdown headings.  A
    plain regular-expression search would mistake those headings for note
    sections and could truncate or corrupt a later replacement.  Section
    discovery therefore ignores headings inside fenced code blocks.
    """
    target = heading.strip()
    match = next(
        (
            (offset, offset + len(line))
            for offset, line, title in _iter_top_level_headings(body)
            if title == target
        ),
        None,
    )
    if match is None:
        return None
    start = match[1]
    next_section = _next_section_start(body, start)
    end = next_section if next_section is not None else len(body)
    return start, end


_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$")
_SECTION_HEADING_RE = re.compile(r"^##\s+([^\n]*?)(?:\n)?$")


def _iter_lines(body: str) -> Iterable[tuple[int, str]]:
    """Yield ``(character_offset, line)`` pairs, retaining line endings."""
    offset = 0
    for line in body.splitlines(keepends=True):
        yield offset, line
        offset += len(line)


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    match = _FENCE_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker), match.group(2)


def _section_title(line: str) -> str | None:
    match = _SECTION_HEADING_RE.match(line)
    return match.group(1).strip() if match else None


def _iter_top_level_headings(body: str) -> Iterable[tuple[int, str, str]]:
    """Yield headings outside fenced code blocks.

    Validate the entire body before yielding any heading: early consumers must
    not miss an unclosed fence later in the note. Markdown permits an unclosed
    fence at EOF, but a section-replacing writer cannot safely infer its intent.
    """
    fence: tuple[str, int] | None = None
    headings: list[tuple[int, str, str]] = []
    for offset, line in _iter_lines(body):
        marker = _fence_marker(line)
        if marker is not None:
            if fence is None and not (marker[0] == "`" and "`" in marker[2]):
                fence = marker[:2]
            elif fence is not None and marker[0] == fence[0] and marker[1] >= fence[1] and not marker[2].strip(" \t"):
                fence = None
            continue
        if fence is None:
            title = _section_title(line)
            if title is not None:
                headings.append((offset, line, title))
    if fence is not None:
        raise OCRGuardError("unclosed Markdown fence; refusing ambiguous section replacement")
    yield from headings


def _next_section_start(body: str, start: int) -> int | None:
    for offset, _line, _title in _iter_top_level_headings(body):
        if offset >= start:
            return offset
    return None


def _section_start(body: str, heading: str) -> tuple[int, int] | None:
    target = heading.strip()
    for offset, line, title in _iter_top_level_headings(body):
        if title == target:
            return offset, offset + len(line)
    return None


def section_content(body: str, heading: str) -> str | None:
    span = find_section(body, heading)
    if not span:
        return None
    return body[span[0] : span[1]]


def replace_section(body: str, heading: str, new_content: str) -> str:
    """Replace the named section's content. Adds a blank line separator after content."""
    span = find_section(body, heading)
    if not span:
        return body
    start, end = span
    # Ensure new content ends with a single trailing newline and a blank line before next section
    new_content = new_content.rstrip("\n") + "\n\n"
    return body[:start] + new_content + body[end:]


def insert_section(
    body: str,
    heading: str,
    new_content: str,
    *,
    before_heading: str | None = None,
) -> str:
    """Insert or replace a section while preserving surrounding note sections.

    ``before_heading`` is used for the optional ``OCR Raw`` section so legacy
    notes keep their original order (raw provenance sits between OCR and
    Vision Description).  Existing sections are replaced using the same
    fence-aware span logic as :func:`replace_section`.
    """
    if find_section(body, heading) is not None:
        return replace_section(body, heading, new_content)

    rendered = f"## {heading}\n\n" + new_content.rstrip("\n") + "\n\n"
    insertion_at: int | None = None
    if before_heading is not None:
        target = _section_start(body, before_heading)
        if target is not None:
            insertion_at = target[0]
    if insertion_at is None:
        if body and not body.endswith("\n"):
            return body + "\n\n" + rendered
        if body and not body.endswith("\n\n"):
            return body + "\n" + rendered
        return body + rendered

    prefix = body[:insertion_at]
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n" if prefix.endswith("\n") else "\n\n"
    return prefix + rendered + body[insertion_at:]


def is_pending(content: str) -> bool:
    """True if the section content begins with a `Pending` marker (italic or bare)."""
    return bool(PENDING_RE.match(content.strip())) if content else True


def normalize_combined_section(body: str) -> str:
    """Split a combined `## OCR / Vision Description` section into two separate sections.

    Some media-description notes were written with the combined format (one heading,
    one `*Both pending.*` marker). This helper rewrites them into two sections so the
    rest of the pipeline (which operates on separate sections) can apply.

    Preserves any contextual prose by attaching it to the Vision Description section.
    """
    combined_span = _section_start(body, "OCR / Vision Description")
    if combined_span is None:
        return body
    heading_start, start = combined_span
    next_section = _next_section_start(body, start)
    end = next_section if next_section is not None else len(body)
    existing = body[start:end].strip()
    # Strip leading "*Both pending.*" or similar markers; keep contextual prose
    existing = re.sub(r"^\*?\s*Both\s+pending\.?\s*\*?\s*", "", existing, flags=re.IGNORECASE).strip()
    if existing:
        vision_placeholder = f"*Pending.* {existing}\n\n"
    else:
        vision_placeholder = "*Pending.*\n\n"
    replacement = (
        "## OCR\n\n"
        "*Pending.*\n\n"
        "## Vision Description\n\n"
        + vision_placeholder
    )
    return body[:heading_start] + replacement + body[end:]


# ----- OCR stage -----


def _clean_tesseract_output(raw_text: str) -> str:
    """Keep the historical Tesseract whitespace normalization in one place."""
    text = raw_text.strip()
    # Collapse multiple blank lines, normalise whitespace
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Drop runs of blank lines at the boundaries
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Collapse internal triple+ blanks to single blank
    cleaned: list[str] = []
    prev_blank = False
    for ln in lines:
        if not ln.strip():
            if prev_blank:
                continue
            prev_blank = True
            cleaned.append("")
        else:
            prev_blank = False
            cleaned.append(ln)
    return "\n".join(cleaned)


def run_tesseract_result(image_path: Path) -> OCRResult:
    """Run Tesseract and return decoded stdout plus its cleaned rendering."""
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not installed (apt install tesseract-ocr)")
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    result = subprocess.run(
        ["tesseract", str(image_path), "-", "-l", "eng", "--psm", "6"],
        capture_output=True,
        text=False,
        timeout=120,
    )
    if result.returncode != 0:
        error_text = _decode_subprocess_output(result.stderr, stream="stderr").strip()
        raise RuntimeError(f"tesseract exited {result.returncode}: {error_text}")
    raw_text = _decode_subprocess_output(result.stdout, stream="stdout")
    return _single_ocr_result("tesseract", raw_text, _clean_tesseract_output(raw_text))


def run_tesseract(image_path: Path) -> str:
    """Compatibility wrapper returning only Tesseract's cleaned OCR text."""
    return run_tesseract_result(image_path).cleaned_text


# Keep tests and downstream callers that monkeypatch the historical string
# runner usable while production enrichment takes the lossless result path.
_ORIGINAL_RUN_TESSERACT = run_tesseract


def _safe_code_fence(text: str, language: str = "text") -> str:
    """Fence arbitrary OCR text without allowing content to close the fence."""
    runs = re.findall(r"`+", text)
    fence_length = max(3, max((len(run) for run in runs), default=0) + 1)
    fence = "`" * fence_length
    separator = "" if text.endswith(("\n", "\r")) else "\n"
    return f"{fence}{language}\n{text}{separator}{fence}\n"


def format_ocr_section(ocr_text: str) -> str:
    """Wrap OCR text in a code block per KB convention; flag empties explicitly."""
    if not ocr_text.strip():
        return "*OCR returned no text; this does not establish that the image contains no text.*\n"
    return _safe_code_fence(ocr_text)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_ocr_raw_section(
    result: OCRResult, *, source: str | None = None
) -> str:
    """Render untouched engine output as provenance, never as OCR truth.

    Each attempt is labelled so a historical compatibility caller can retain
    multiple attempts independently.  The text is decoded subprocess/API
    output, not a byte-for-byte stdout artifact; its UTF-8 length and digest
    make that distinction explicit while preserving the exact decoded string.
    """
    source_label = (
        json.dumps(source, ensure_ascii=False)
        if source
        else "captured local media referenced by this note's `local_path`"
    )
    attempts = result.attempts or (
        OCRAttempt(
            engine=result.engine,
            raw_text=result.raw_text,
            cleaned_text=result.cleaned_text,
        ),
    )
    parts = [
        "Raw OCR engine output is retained for provenance/audit only; it is not truth, a transcription certification, or a vision description.",
        "",
        f"- **Source:** {source_label}; engine is labelled per attempt below.",
        "- **Capture:** subprocess stdout, where applicable, is decoded as UTF-8 with newline bytes preserved; provider output retains the text exposed by its wrapper. Renderer cleanup is not applied to this section.",
        "",
    ]
    for index, attempt in enumerate(attempts, 1):
        raw_text = attempt.raw_text
        parts.extend(
            [
                f"### Attempt {index}: `{attempt.engine}`",
                "",
                f"- **Raw UTF-8 character length:** {len(raw_text)}",
                f"- **Raw UTF-8 SHA-256:** `{_sha256_text(raw_text)}`",
                f"- **Derived cleaned UTF-8 character length:** {len(attempt.cleaned_text)}",
                f"- **Derived cleaned UTF-8 SHA-256:** `{_sha256_text(attempt.cleaned_text)}`",
                "",
                _safe_code_fence(raw_text),
                "",
            ]
        )
    return "\n".join(parts).rstrip("\n") + "\n"


# ----- Unlimited-OCR (local via llama.cpp; explicit engine selection) -----
# This optional adapter retains structured text/bounding-box/region output when a
# caller has configured a compatible local runtime. Override all runtime paths via
# KB_OCR_* environment variables; no engine is selected automatically.

_KB_OCR_HOME = Path(os.environ.get("KB_OCR_HOME", str(Path.home() / ".local/share/kb-ocr")))
UNLIMITED_OCR_BIN = os.environ.get("KB_OCR_LLAMA_BIN", str(_KB_OCR_HOME / "llama" / "llama-mtmd-cli"))
UNLIMITED_OCR_MODEL = os.environ.get("KB_OCR_MODEL", str(_KB_OCR_HOME / "models" / "Unlimited-OCR-Q4_K_M.gguf"))
UNLIMITED_OCR_MMPROJ = os.environ.get("KB_OCR_MMPROJ", str(_KB_OCR_HOME / "models" / "mmproj-Unlimited-OCR-F16.gguf"))
UNLIMITED_OCR_PROMPT = os.environ.get("KB_OCR_PROMPT", "Free OCR.")
UNLIMITED_OCR_TIMEOUT = int(os.environ.get("KB_OCR_TIMEOUT", "300"))

# Grounding tokens the model emits: <|det|>LABEL [x,y,x,y]<|/det|> optionally followed
# by the region's text. Text-bearing labels keep their text; pure-graphic regions
# collapse to a [label] marker.
_DET_RE = re.compile(r"<\|det\|>\s*([A-Za-z_]+)\s*\[[^\]]*\]\s*<\|/det\|>")
_GRAPHIC_LABELS = {"chart", "figure", "image", "table", "diagram", "graph", "plot"}
# The model's own meta-statements that are noise, not extracted content.
_META_NOISE_RE = re.compile(r"^\[?\s*no text (detected|found|present)[.\]]*\s*$", re.IGNORECASE)


def clean_unlimited_output(raw: str) -> str:
    """Strip the model's grounding tokens into readable text; mark graphic regions and
    drop recognized meta-noise lines while preserving source text in any script."""
    def _repl(m: "re.Match[str]") -> str:
        label = m.group(1).lower()
        return f"[{label}]" if label in _GRAPHIC_LABELS else ""

    text = _DET_RE.sub(_repl, raw)
    text = re.sub(r"<\|[^|]*\|>", "", text)  # drop any remaining special tokens
    cleaned: list[str] = []
    prev_blank = False
    for ln in (line.rstrip() for line in text.splitlines()):
        if _META_NOISE_RE.match(ln.strip()):
            continue  # model meta-noise; preserve source text in every script
        if not ln.strip():
            if prev_blank:
                continue
            prev_blank = True
            cleaned.append("")
        else:
            prev_blank = False
            cleaned.append(ln)
    return "\n".join(cleaned).strip()


def run_unlimited_ocr_result(image_path: Path) -> OCRResult:
    """Run local Unlimited-OCR and retain stdout before rendering cleanup.

    Greedy decoding (--temp 0) is REQUIRED: the Q4 quant otherwise degenerates into
    repetition loops. The model's custom chat template requires --jinja.
    """
    bin_path = Path(UNLIMITED_OCR_BIN)
    model = Path(UNLIMITED_OCR_MODEL)
    mmproj = Path(UNLIMITED_OCR_MMPROJ)
    for label, pth in (("binary", bin_path), ("model", model), ("mmproj", mmproj)):
        if not pth.exists():
            raise RuntimeError(
                f"Unlimited-OCR {label} not found: {pth} "
                f"(set KB_OCR_HOME or KB_OCR_LLAMA_BIN/KB_OCR_MODEL/KB_OCR_MMPROJ)"
            )
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{bin_path.parent}:{env.get('LD_LIBRARY_PATH', '')}"
    cmd = [
        str(bin_path), "-m", str(model), "--mmproj", str(mmproj),
        "--image", str(image_path), "-p", UNLIMITED_OCR_PROMPT,
        "-n", "2048", "--jinja", "--no-warmup", "--temp", "0",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=False,
        timeout=UNLIMITED_OCR_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        error_text = _decode_subprocess_output(result.stderr, stream="stderr")
        raise RuntimeError(
            f"llama-mtmd-cli exited {result.returncode}: {error_text.strip()[-300:]}"
        )
    raw_text = _decode_subprocess_output(result.stdout, stream="stdout")
    return _single_ocr_result("unlimited", raw_text, clean_unlimited_output(raw_text))


def run_unlimited_ocr(image_path: Path) -> str:
    """Compatibility wrapper returning only the cleaned Unlimited-OCR text."""
    return run_unlimited_ocr_result(image_path).cleaned_text


_ORIGINAL_RUN_UNLIMITED_OCR = run_unlimited_ocr


# ----- Unlimited-OCR via HF transformers + crop_mode (explicit local adapter) -----
# Runs the configured helper for dense/wide text; output is post-cleaned of the
# model's evaluation commentary. Runtime availability and performance are
# environment-specific and are not used for automatic routing.

_KB_OCR_HF_VENV = os.environ.get(
    "KB_OCR_HF_VENV", str(Path.home() / ".local/share/kb-ocr/hf-venv/bin/python")
)
_KB_OCR_HF_HELPER = os.environ.get(
    "KB_OCR_HF_HELPER", str(Path(__file__).resolve().parent / "kb-ocr-hf-infer.py")
)
UNLIMITED_HF_TIMEOUT = int(os.environ.get("KB_OCR_HF_TIMEOUT", "600"))

# The model (with the "Free OCR." prompt) emits GT-comparison eval-commentary mixed with
# the real rows. These phrases mark that hallucinated prose; drop any line containing one.
_HF_EVAL_NOISE_RE = re.compile(
    r"ground truth|according to rule|stylistic or background|consists of (?:no|four|only)\b|"
    r"must be ignored|complies with|violat\w* the rule|hallucinat|the ocr (?:result|content|output)|"
    r"is (?:consistent|inconsistent) with|placeholder underscore|underscore & line|"
    r"contains no text|no characters|the (?:image|provided ocr)",
    re.IGNORECASE,
)


# Standalone non-informative markers / UI chrome the model emits on screenshots:
# bare [Non-Text]/[NO TEXT]/[image] tags, a "Text:" eval prefix, or pure-symbol lines.
_HF_LINE_NOISE_RE = re.compile(
    r"^(?:\[(?:non[\s-]?text|no text|image)\]|text:\s*_*|[\s√✓✗•·+#©®*_|<>←→-]{1,4})$",
    re.IGNORECASE,
)


def clean_unlimited_hf_output(raw: str) -> str:
    """Strip grounding tokens (via clean_unlimited_output), drop eval-commentary and
    marker/UI-noise lines, and collapse consecutive duplicate lines."""
    text = clean_unlimited_output(raw)
    deduped: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or _HF_EVAL_NOISE_RE.search(ln) or _HF_LINE_NOISE_RE.match(s):
            continue
        if not deduped or deduped[-1] != ln:
            deduped.append(ln)
    return "\n".join(deduped).strip()


def run_unlimited_hf_ocr_result(image_path: Path) -> OCRResult:
    """Run HF Unlimited-OCR and retain helper stdout before HF cleanup."""
    venv = Path(_KB_OCR_HF_VENV)
    helper = Path(_KB_OCR_HF_HELPER)
    for label, pth in (("venv python", venv), ("helper", helper)):
        if not pth.exists():
            raise RuntimeError(f"unlimited-hf {label} not found: {pth} (set KB_OCR_HF_VENV/KB_OCR_HF_HELPER)")
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    result = subprocess.run(
        [str(venv), str(helper), str(image_path)],
        capture_output=True,
        text=False,
        timeout=UNLIMITED_HF_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        error_text = _decode_subprocess_output(result.stderr, stream="stderr")
        raise RuntimeError(
            f"unlimited-hf infer exited {result.returncode}: {error_text.strip()[-300:]}"
        )
    raw_text = _decode_subprocess_output(result.stdout, stream="stdout")
    return _single_ocr_result("unlimited-hf", raw_text, clean_unlimited_hf_output(raw_text))


def run_unlimited_hf_ocr(image_path: Path) -> str:
    """Compatibility wrapper returning only cleaned HF Unlimited-OCR text."""
    return run_unlimited_hf_ocr_result(image_path).cleaned_text


_ORIGINAL_RUN_UNLIMITED_HF_OCR = run_unlimited_hf_ocr


def run_auto_ocr_result(image_path: Path) -> OCRResult:
    """Compatibility helper for the retired automatic fallback route.

    The active CLI rejects ``--ocr-engine auto``; keeping this function avoids
    breaking direct callers that still import the historical helper.
    """
    if run_unlimited_ocr is _ORIGINAL_RUN_UNLIMITED_OCR:
        fast = run_unlimited_ocr_result(image_path)
    else:
        fast = _coerce_legacy_result("unlimited", run_unlimited_ocr(image_path))
    if fast.cleaned_text.strip():
        return fast
    if run_unlimited_hf_ocr is _ORIGINAL_RUN_UNLIMITED_HF_OCR:
        fallback = run_unlimited_hf_ocr_result(image_path)
    else:
        fallback = _coerce_legacy_result(
            "unlimited-hf", run_unlimited_hf_ocr(image_path)
        )
    return OCRResult(
        engine=fallback.engine,
        raw_text=fallback.raw_text,
        cleaned_text=fallback.cleaned_text,
        attempts=fast.attempts + fallback.attempts,
    )


def run_auto_ocr(image_path: Path) -> str:
    """Compatibility wrapper returning only the selected cleaned OCR text."""
    return run_auto_ocr_result(image_path).cleaned_text


_ORIGINAL_RUN_AUTO_OCR = run_auto_ocr


# ----- Mistral OCR (hosted, explicit opt-in) -----

MISTRAL_OCR_MODEL = os.environ.get("KB_MISTRAL_OCR_MODEL", "").strip()
_MISTRAL_MIME = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "tiff": "tiff", "gif": "gif"}


def _mistral_api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if key:
        return key
    raise RuntimeError("MISTRAL_API_KEY is not set in the environment")


def run_mistral_ocr_result(image_path: Path) -> OCRResult:
    """Keep provider-exposed page markdown before deriving the display text.

    Each page is retained separately in ``attempts`` so joining pages does not
    erase their boundaries. This is page markdown, not the HTTP envelope or
    a copy of the request (which contains credentials and image bytes).
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    if not MISTRAL_OCR_MODEL:
        raise RuntimeError("KB_MISTRAL_OCR_MODEL is not set; select a model explicitly")
    key = _mistral_api_key()
    mime = _MISTRAL_MIME.get(image_path.suffix.lstrip(".").lower(), "png")
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = {
        "model": MISTRAL_OCR_MODEL,
        "document": {"type": "image_url", "image_url": f"data:image/{mime};base64,{b64}"},
    }
    req = urllib.request.Request(
        "https://api.mistral.ai/v1/ocr",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_bytes = resp.read(MAX_MISTRAL_RESPONSE_BYTES + 1)
            if len(response_bytes) > MAX_MISTRAL_RESPONSE_BYTES:
                raise RuntimeError("Mistral OCR response exceeds the 8 MiB input limit")
            data = json.loads(response_bytes)
    except urllib.error.HTTPError as exc:
        # Do not read or echo provider error bodies: they may contain request
        # data, and are not OCR evidence. The HTTP status is enough to stop.
        exc.close()
        raise RuntimeError(f"Mistral OCR HTTP {exc.code}") from None
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise RuntimeError("Mistral OCR response must contain a page list")
    pages = []
    for page in data["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("markdown"), str):
            raise RuntimeError("Mistral OCR page markdown must be text")
        pages.append(page["markdown"])
    cleaned = _clean_mistral_pages(pages)
    attempts = tuple(
        OCRAttempt(f"mistral page {index}", raw, _clean_mistral_pages([raw]))
        for index, raw in enumerate(pages, 1)
    )
    return OCRResult("mistral", "\n\n".join(pages), cleaned, attempts)


def _clean_mistral_pages(pages: list[str]) -> str:
    """Preserve historical display normalization, never raw evidence."""
    text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "[figure]", text).strip()


def run_mistral_ocr(image_path: Path) -> str:
    """Compatibility wrapper returning only cleaned Mistral page markdown."""
    return run_mistral_ocr_result(image_path).cleaned_text


_ORIGINAL_RUN_MISTRAL_OCR = run_mistral_ocr


# ----- Vision stage -----


def run_gemini_vision(image_path: Path) -> str:
    """Retired compatibility entry point for the former hosted vision route."""
    reject_retired_vision_route("run_gemini_vision")


# ----- enrichment driver -----


def _relative_note(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _new_result(note_path: Path, root: Path) -> dict[str, Any]:
    return {
        "note": _relative_note(note_path, root),
        "ocr": None,
        "vision": None,
        "skipped": False,
        "error": None,
        "written": False,
    }


def _summary(
    root: Path | str,
    results: list[dict[str, Any]],
    *,
    dry_run: bool,
    ocr_engine: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build one stable CLI receipt for both success and guarded failures."""
    return {
        "kb_root": str(root),
        "stages": {"ocr": True, "vision": False, "ocr_engine": ocr_engine},
        "dry_run": dry_run,
        "processed": len(results),
        "written": sum(1 for item in results if item.get("written")),
        "ocr_ok": sum(1 for item in results if (item.get("ocr") or "").startswith("ok")),
        "vision_ok": 0,
        "results": results,
        "error": error or next((item["error"] for item in results if item.get("error")), None),
    }


def _ocr_plan(snapshot: NoteSnapshot) -> tuple[str, bool]:
    """Return normalized planning body and whether OCR is still pending."""
    normalized_body = normalize_combined_section(snapshot.body)
    ocr_section = section_content(normalized_body, "OCR")
    if ocr_section is None:
        raise OCRGuardError("media-description note has no ## OCR section")
    return normalized_body, is_pending(ocr_section)


def _run_selected_ocr_result(image_path: Path, ocr_engine: str) -> OCRResult:
    """Dispatch to an engine while retaining raw and rendered OCR channels."""
    if ocr_engine == "auto":
        raise OCRGuardError(
            "OCR engine 'auto' is retired; select one explicit engine"
        )
    if ocr_engine not in {"unlimited", "unlimited-hf", "tesseract", "mistral"}:
        raise OCRGuardError(f"unsupported OCR engine: {ocr_engine}")
    if ocr_engine == "tesseract":
        if run_tesseract is not _ORIGINAL_RUN_TESSERACT:
            return _coerce_legacy_result("tesseract", run_tesseract(image_path))
        return run_tesseract_result(image_path)
    if ocr_engine == "mistral":
        if run_mistral_ocr is not _ORIGINAL_RUN_MISTRAL_OCR:
            return _coerce_legacy_result("mistral", run_mistral_ocr(image_path))
        return run_mistral_ocr_result(image_path)
    if ocr_engine == "unlimited-hf":
        if run_unlimited_hf_ocr is not _ORIGINAL_RUN_UNLIMITED_HF_OCR:
            return _coerce_legacy_result(
                "unlimited-hf", run_unlimited_hf_ocr(image_path)
            )
        return run_unlimited_hf_ocr_result(image_path)
    if ocr_engine == "unlimited":
        if run_unlimited_ocr is not _ORIGINAL_RUN_UNLIMITED_OCR:
            return _coerce_legacy_result("unlimited", run_unlimited_ocr(image_path))
        return run_unlimited_ocr_result(image_path)
    raise OCRGuardError(f"unsupported OCR engine: {ocr_engine}")


def _run_selected_ocr(image_path: Path, ocr_engine: str) -> str:
    """Compatibility dispatcher returning only the derived cleaned text."""
    return _run_selected_ocr_result(image_path, ocr_engine).cleaned_text


def _capture_snapshot(note_path: Path, root: Path) -> NoteSnapshot:
    """Take the short shared-lock snapshot required before OCR."""
    with _write_guard.write_lock(root, root / LOCK_NAME, nonblocking=True):
        return _snapshot_note(note_path, root)


def _commit_ocr(
    snapshot: NoteSnapshot,
    root: Path,
    ocr_markdown: str,
    *,
    raw_markdown: str | None = None,
    force: bool,
) -> bool:
    """Revalidate the snapshot and atomically commit OCR under the lock."""
    with _write_guard.write_lock(root, root / LOCK_NAME, nonblocking=True):
        try:
            current = _snapshot_note(snapshot.note_path, root)
        except IneligibleNoteError as exc:
            raise StaleSnapshotError(
                f"note eligibility changed while OCR was running: {exc}"
            ) from exc
        if not _same_snapshot(snapshot, current):
            raise StaleSnapshotError(
                "note or image changed while OCR was running; refusing stale commit"
            )

        normalized_body, pending = _ocr_plan(current)
        if not pending and not force:
            raise StaleSnapshotError(
                "OCR section is no longer pending; refusing stale commit"
            )
        updated_body = replace_section(normalized_body, "OCR", ocr_markdown)
        if raw_markdown is not None:
            updated_body = insert_section(
                updated_body,
                "OCR Raw",
                raw_markdown,
                before_heading="Vision Description",
            )
        # A combined-section normalization or any other plan-only change is
        # never published by itself.  Only a successful OCR result can reach
        # this writer, and a byte-identical result remains idempotent.
        if updated_body == current.body:
            return False
        try:
            _write_guard.atomic_write(
                current.note_path,
                current.frontmatter + updated_body,
                prefix=WRITE_PREFIX,
            )
        except Exception as exc:
            raise OCRGuardError(f"OCR commit failed: {exc}") from exc
        return True


def _preflight_notes(
    notes: Iterable[Path], root: Path
) -> None:
    """Validate every selected note before any provider or local engine call."""
    with _write_guard.write_lock(root, root / LOCK_NAME, nonblocking=True):
        for note in notes:
            try:
                snapshot = _snapshot_note(note, root)
            except IneligibleNoteError:
                # A valid kb-bot media note outside the captured-image lane is
                # an ordinary skip, matching the historical helper contract.
                continue
            # Missing OCR headings are a read-only preflight failure, rather
            # than a reason to invoke an engine and discover it later.
            _ocr_plan(snapshot)


def enrich_note(
    note_path: Path,
    kb_root: Path,
    do_ocr: bool,
    do_vision: bool,
    dry_run: bool,
    ocr_engine: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if do_vision:
        reject_retired_vision_route("enrich_note(do_vision=True)")

    root = _write_guard.canonical_root(kb_root)
    canonical_note = canonical_note_path(note_path, root)
    result = _new_result(canonical_note, root)
    try:
        snapshot = _capture_snapshot(canonical_note, root)
    except IneligibleNoteError as exc:
        result["skipped"] = True
        result["ocr"] = f"ineligible (skipped): {exc}"
        return result
    _normalized_body, pending = _ocr_plan(snapshot)

    if do_ocr and not pending and not force:
        result["skipped"] = True
        result["ocr"] = "already enriched (skipped)"
        return result
    if not do_ocr:
        result["skipped"] = True
        result["ocr"] = "OCR stage not requested"
        return result
    if dry_run:
        result["ocr"] = (
            f"dry-run: would run {ocr_engine} OCR"
            if ocr_engine
            else "dry-run: OCR engine not selected"
        )
        return result

    try:
        ocr_result = _run_selected_ocr_result(snapshot.image_path, ocr_engine)
        _validate_result_size(ocr_result)
    except Exception as exc:
        result["ocr"] = f"error: {exc}"
        result["error"] = f"OCR error: {exc}"
        return result

    ocr_text = ocr_result.cleaned_text
    ocr_markdown = format_ocr_section(ocr_text)
    raw_markdown = format_ocr_raw_section(
        ocr_result,
        source=snapshot.metadata.get("local_path"),
    )
    try:
        result["written"] = _commit_ocr(
            snapshot,
            root,
            ocr_markdown,
            raw_markdown=raw_markdown,
            force=force,
        )
    except OCRGuardError as exc:
        result["error"] = f"commit error: {exc}"
        result["ocr"] = f"error: {exc}"
        return result
    result["ocr"] = f"ok ({len(ocr_text)} chars)"
    return result


def find_media_notes(kb_root: Path, topic: str | None, single: Path | None) -> list[Path]:
    root = _write_guard.canonical_root(kb_root)
    if single is not None:
        return [canonical_note_path(single, root)]
    topic = _validate_topic(topic)
    topics = root / "topics"
    try:
        _write_guard._walk_no_symlinks(topics, allow_final_missing=True)
    except FileNotFoundError:
        return []
    if topic:
        scopes = [root / "topics" / topic / "notes" / "media"]
    else:
        scopes = []
        for candidate in sorted(topics.iterdir()):
            # Avoid pathlib's implicit symlink traversal; canonical_note_path
            # remains authoritative for any files discovered below.
            if not candidate.is_dir():
                continue
            scopes.append(candidate / "notes" / "media")
    out: list[Path] = []
    for scope in scopes:
        try:
            _write_guard._walk_no_symlinks(scope, allow_final_missing=True)
        except FileNotFoundError:
            continue
        for candidate in sorted(scope.glob("*.md")):
            out.append(canonical_note_path(candidate, root))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--topic", help="Limit to a single topic (slug)")
    p.add_argument("--note", help="Run on a single media-description note path (relative to kb-root or absolute)")
    p.add_argument("--limit", type=int, default=0, help="Process at most N notes (0 = unlimited)")
    p.add_argument("--ocr-only", action="store_true")
    p.add_argument("--vision-only", action="store_true")
    p.add_argument("--both", action="store_true")
    p.add_argument(
        "--ocr-engine",
        choices=["auto", "unlimited", "unlimited-hf", "tesseract", "mistral"],
        default=None,
        help="Explicit OCR engine: 'mistral' (hosted API), "
        "'unlimited' (configured local executable), "
        "'unlimited-hf' (configured offline helper), or 'tesseract' (offline). "
        "'auto' is a retired compatibility alias and is rejected; no automatic "
        "engine fallback is performed.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview pending OCR work without invoking OCR engines or writing files",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run enrichment even if a section is already filled (overwrites it).",
    )
    args = p.parse_args(argv)

    if args.ocr_engine == "auto":
        p.error("OCR engine 'auto' is retired; select one explicit engine")
    if args.ocr_only and args.vision_only:
        p.error("--ocr-only and --vision-only are mutually exclusive")
    if args.vision_only or args.both or not args.ocr_only:
        p.error(RETIRED_VISION_MESSAGE)
    if not args.dry_run and not args.ocr_engine:
        p.error("--ocr-engine is required for a non-dry OCR run")
    do_ocr = True
    do_vision = False

    try:
        # canonical_root intentionally rejects aliases instead of resolving
        # them, so a note's snapshot and final commit retain one lexical root.
        kb_root = _write_guard.canonical_root(Path(args.kb_root).expanduser())
        topic = _validate_topic(args.topic)
    except (OSError, _write_guard.WriteGuardError) as exc:
        print(json.dumps(_summary(
            args.kb_root,
            [],
            dry_run=args.dry_run,
            ocr_engine=args.ocr_engine,
            error=f"preflight error: {exc}",
        ), indent=2))
        return 2

    single: Path | None = None
    if args.note:
        single = Path(args.note)
        if not single.is_absolute():
            single = kb_root / single
    try:
        notes = find_media_notes(kb_root, topic, single)
    except (OSError, _write_guard.WriteGuardError) as exc:
        result = _new_result(single or kb_root, kb_root) if single else None
        if result is not None:
            result["error"] = f"preflight error: {exc}"
        print(json.dumps(_summary(
            kb_root,
            [result] if result else [],
            dry_run=args.dry_run,
            ocr_engine=args.ocr_engine,
            error=f"preflight error: {exc}",
        ), indent=2))
        return 2
    if args.limit > 0:
        notes = notes[: args.limit]

    # Validate every selected note and image while no engine is running.  A
    # malformed later candidate therefore cannot be discovered after an
    # earlier candidate has already spent provider/CPU work.
    try:
        _preflight_notes(notes, kb_root)
    except (OSError, _write_guard.WriteGuardError) as exc:
        results = []
        for note in notes:
            result = _new_result(note, kb_root)
            result["error"] = f"preflight error: {exc}"
            results.append(result)
        print(json.dumps(_summary(
            kb_root,
            results,
            dry_run=args.dry_run,
            ocr_engine=args.ocr_engine,
            error=f"preflight error: {exc}",
        ), indent=2))
        return 2

    results: list[dict[str, Any]] = []
    had_error = False
    for n in notes:
        try:
            r = enrich_note(
                n,
                kb_root,
                do_ocr=do_ocr,
                do_vision=do_vision,
                dry_run=args.dry_run,
                ocr_engine=args.ocr_engine,
                force=args.force,
            )
        except (OSError, UnicodeError, _write_guard.WriteGuardError) as exc:
            r = _new_result(n, kb_root)
            r["error"] = f"preflight/commit error: {exc}"
            r["written"] = False
        if r.get("error"):
            had_error = True
        results.append(r)

    print(json.dumps(_summary(
        kb_root,
        results,
        dry_run=args.dry_run,
        ocr_engine=args.ocr_engine,
    ), indent=2))
    return 2 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
