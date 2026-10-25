#!/usr/bin/env python3
"""Prepare bounded, local-only evidence from one staged KB media file.

This helper deliberately has no network transport and does not publish media,
source notes, or interpretations.  It accepts one already-staged image or
video, verifies its supplied SHA-256, and writes temporary derived evidence
under ``_tools/staging/media/``.  The JSON receipt is the durable control
record; the derived files remain private scratch until an independently
reviewed cleanup invocation removes them.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
from queue import Empty
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_VIDEO_DURATION_SECONDS = 180.0
MAX_FRAMES = 40
DEFAULT_MAX_FRAMES = 40
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 600.0
MAX_FRAME_WIDTH = 1920
MAX_FRAME_HEIGHT = 1080
MAX_FRAME_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
ASR_TIMESTAMP_TOLERANCE_SECONDS = 1.0
ASR_DEGENERATE_REPEAT_MIN_SEGMENTS = 3
ASR_DEGENERATE_REPEAT_MAX_WORDS = 2
ASR_DEGENERATE_REPEAT_MAX_CHARS = 24
ASR_REVIEWED_DISPOSITION = "unusable-asr"

STAGING_RELATIVE = Path("_tools") / "staging" / "media"
RECEIPT_NAME = "receipt.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv",
})

_WRITE_GUARD: Any = None


class MediaInspectError(ValueError):
    """A bounded media-inspection or cleanup contract violation."""


class MediaInspectMissing(MediaInspectError):
    """A checked path is absent (used for safe cleanup idempotency)."""


class MediaInspectTimeout(MediaInspectError):
    """The extraction wall-clock budget expired."""


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    sha256: str
    size: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True)
class VideoProbe:
    duration_seconds: float
    width: int | None
    height: int | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    raw: dict[str, Any]


def _absolute_lexical(path: Path | str, base: Path | None = None) -> Path:
    """Build an absolute path while preserving and rejecting ``..``."""
    value = Path(path).expanduser()
    if "\x00" in str(value):
        raise MediaInspectError("path contains NUL")
    if "\\" in str(value):
        raise MediaInspectError("backslash paths are not allowed")
    if not value.is_absolute():
        value = (base if base is not None else Path.cwd()) / value
    if ".." in value.parts:
        raise MediaInspectError("parent traversal is not allowed")
    return value


def _write_guard() -> Any:
    """Load the KB's shared no-symlink/root/file guards once."""
    global _WRITE_GUARD
    if _WRITE_GUARD is None:
        spec = importlib.util.spec_from_file_location(
            "kb_media_inspect_write_guard", Path(__file__).with_name("kb-write-guard.py")
        )
        if spec is None or spec.loader is None:
            raise MediaInspectError("shared KB write guard is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _WRITE_GUARD = module
    return _WRITE_GUARD


def _walk_no_symlinks(path: Path, *, allow_final_missing: bool = False) -> None:
    try:
        _write_guard()._walk_no_symlinks(path, allow_final_missing=allow_final_missing)
    except FileNotFoundError:
        raise MediaInspectError(f"path does not exist: {path}") from None
    except (OSError, ValueError) as exc:
        raise MediaInspectError(str(exc)) from exc


def canonical_kb_root(value: Path | str) -> Path:
    """Return an existing, non-symlink KB root directory."""
    try:
        return _write_guard().canonical_root(value)
    except (OSError, ValueError) as exc:
        raise MediaInspectError(str(exc)) from exc


def _strip_vault_prefix(path: Path) -> Path:
    """Accept the two path spellings used by KB tools.

    Callers may pass ``_tools/staging/media/...`` relative to the selected KB
    root or the vault-relative spelling ``knowledgebase/_tools/...``.  The
    latter is only a display prefix; it is not interpreted as a directory
    below an arbitrary root.
    """
    parts = path.parts
    if parts and parts[0] == "knowledgebase":
        return Path(*parts[1:])
    return path


def scoped_path(value: Path | str, root: Path, *, allow_missing: bool = False) -> Path:
    """Resolve a path below ``root`` without following symlink components."""
    raw = _absolute_lexical(value, root)
    if not Path(value).expanduser().is_absolute():
        # ``_absolute_lexical`` has already joined root.  The vault prefix is
        # removed before the join so ``knowledgebase/...`` cannot escape the
        # selected test/root directory.
        raw_value = Path(value).expanduser()
        if raw_value.parts and raw_value.parts[0] == "knowledgebase":
            raw = root / _strip_vault_prefix(raw_value)
    try:
        raw.relative_to(root)
    except ValueError as exc:
        raise MediaInspectError("path escapes the KB root") from exc
    _walk_no_symlinks(raw, allow_final_missing=allow_missing)
    return raw


def staging_root(root: Path) -> Path:
    """Validate the private media scratch root without creating it."""
    path = root / STAGING_RELATIVE
    _walk_no_symlinks(path)
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise MediaInspectError("KB staging media path must be a directory")
    return path


def _under(path: Path, parent: Path, *, strict: bool = False) -> bool:
    try:
        rel = path.relative_to(parent)
    except ValueError:
        return False
    return bool(rel.parts) if strict else True


def scoped_staged_input(value: Path | str, root: Path) -> Path:
    path = scoped_path(value, root)
    scratch = staging_root(root)
    if not _under(path, scratch, strict=True):
        raise MediaInspectError("input must be under KB _tools/staging/media/")
    return path


def scoped_output_dir(value: Path | str, root: Path) -> Path:
    """Validate a new scratch directory path, but do not create it."""
    path = scoped_path(value, root, allow_missing=True)
    scratch = staging_root(root)
    if not _under(path, scratch, strict=True):
        raise MediaInspectError("output directory must be under KB _tools/staging/media/")
    if path == scratch:
        raise MediaInspectError("output directory must be a fresh child of media staging")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            raise MediaInspectError("output directory may not be a symlink")
        raise MediaInspectError("output directory already exists")
    # Existing parent components are checked, but the helper will only create
    # this one final directory.  This prevents a typo from creating a broad
    # hierarchy elsewhere in the KB.
    parent = path.parent
    _walk_no_symlinks(parent)
    parent_info = os.stat(parent, follow_symlinks=False)
    if not stat.S_ISDIR(parent_info.st_mode):
        raise MediaInspectError("output directory parent must be a directory")
    return path


def _open_regular(path: Path, *, flags: int = os.O_RDONLY) -> tuple[int, os.stat_result]:
    try:
        return _write_guard()._open_regular(path, flags=flags | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError as exc:
        raise MediaInspectMissing(f"path does not exist: {path}") from exc
    except (OSError, ValueError) as exc:
        raise MediaInspectError(str(exc)) from exc


def read_snapshot(path: Path, expected_sha256: str, *, max_bytes: int = MAX_INPUT_BYTES) -> FileSnapshot:
    """Hash one stable, singly-linked regular file and compare its digest."""
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise MediaInspectError("supplied SHA-256 must be 64 lowercase hexadecimal characters")
    descriptor, before = _open_regular(path)
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise MediaInspectError(f"input exceeds {max_bytes} bytes")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if before.st_ino != after.st_ino or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise MediaInspectError("input changed while hashing")
    if size < 1:
        raise MediaInspectError("input must not be empty")
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise MediaInspectError(f"input SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    return FileSnapshot(path, actual, size, before.st_ino, before.st_mtime_ns)


def verify_snapshot_unchanged(snapshot: FileSnapshot) -> None:
    descriptor, info = _open_regular(snapshot.path)
    digest = hashlib.sha256()
    size = 0
    try:
        if info.st_ino != snapshot.inode or info.st_size != snapshot.size or info.st_mtime_ns != snapshot.mtime_ns:
            raise MediaInspectError("input changed after validation")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, snapshot.size + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > snapshot.size:
                raise MediaInspectError("input changed after validation")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        after.st_ino != snapshot.inode
        or after.st_size != snapshot.size
        or after.st_mtime_ns != snapshot.mtime_ns
        or size != snapshot.size
    ):
        raise MediaInspectError("input changed after validation")
    if digest.hexdigest() != snapshot.sha256:
        raise MediaInspectError("input bytes changed after validation")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MediaInspectTimeout("extraction timeout exceeded")
    return remaining


def _run_process(command: list[str], *, timeout: float, label: str) -> subprocess.CompletedProcess[bytes]:
    """Run a local decoder with no inherited stdin and a bounded timeout."""
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env={**os.environ, "AV_LOG_FORCE_NOCOLOR": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaInspectTimeout(f"{label} timed out") from exc
    except OSError as exc:
        raise MediaInspectError(f"{label} unavailable: {exc}") from exc


def _decode_json_output(result: subprocess.CompletedProcess[bytes], label: str) -> dict[str, Any]:
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise MediaInspectError(f"{label} failed: {stderr[:500] or 'exit status ' + str(result.returncode)}")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise MediaInspectError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MediaInspectError(f"{label} returned a non-object")
    return payload


def probe_video(path: Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> VideoProbe:
    command = [
        "ffprobe", "-hide_banner", "-v", "error",
        "-protocol_whitelist", "file,pipe",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ]
    result = _run_process(command, timeout=timeout, label="ffprobe")
    payload = _decode_json_output(result, "ffprobe")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise MediaInspectError("ffprobe did not return streams")
    video_stream = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise MediaInspectError("input has no video stream")
    audio_stream = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), None)
    format_block = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration_value = video_stream.get("duration") or format_block.get("duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise MediaInspectError("video duration is unavailable") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaInspectError("video duration must be finite and greater than zero")
    if duration > MAX_VIDEO_DURATION_SECONDS:
        raise MediaInspectError(
            f"video duration {duration:.3f}s exceeds {MAX_VIDEO_DURATION_SECONDS:.0f}s bound"
        )
    width = _positive_int(video_stream.get("width"))
    height = _positive_int(video_stream.get("height"))
    return VideoProbe(
        duration_seconds=duration,
        width=width,
        height=height,
        has_audio=audio_stream is not None,
        video_codec=_optional_text(video_stream.get("codec_name")),
        audio_codec=_optional_text(audio_stream.get("codec_name")) if audio_stream else None,
        raw=payload,
    )


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def inspect_image(path: Path) -> tuple[int | None, int | None]:
    """Read image dimensions without writing or interpreting the image."""
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        raise MediaInspectError(f"Pillow is required for image inspection: {exc}") from exc
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise MediaInspectError(f"image decoding failed: {exc}") from exc
    if width < 1 or height < 1:
        raise MediaInspectError("image dimensions must be positive")
    return int(width), int(height)


def media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    raise MediaInspectError("input extension is not a supported staged image or video")


def validate_origin_url(value: str) -> str:
    """Validate provenance shape without fetching or resolving the URL."""
    if not isinstance(value, str) or not value.strip() or any(ord(char) <= 32 for char in value):
        raise MediaInspectError("origin URL must be a non-empty HTTP(S) URL without whitespace")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError as exc:
        raise MediaInspectError("origin URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
        raise MediaInspectError("origin URL must be HTTP(S) without credentials")
    return value


def regular_timestamps(
    duration: float,
    max_frames: int,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> list[float]:
    """Return evenly spaced timestamps strictly before the video end."""
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise MediaInspectError(f"max frames must be an integer from 1 to {MAX_FRAMES}")
    if not math.isfinite(duration) or duration <= 0:
        raise MediaInspectError("duration must be finite and greater than zero")
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise MediaInspectError("sampling interval must be finite and greater than zero")
    count = min(max_frames, max(1, math.ceil(duration / interval_seconds)))
    # Sampling at the beginning of each equal-duration bucket gives stable
    # timestamps and avoids asking a decoder for exactly duration_seconds.
    return [duration * index / count for index in range(count)]


def _mkdir_fresh(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError as exc:
        raise MediaInspectError("output directory already exists") from exc
    except OSError as exc:
        raise MediaInspectError(f"cannot create fresh output directory: {exc}") from exc
    _walk_no_symlinks(path)
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise MediaInspectError("created output path is not a directory")


def _safe_output_file(path: Path) -> None:
    """Ensure a decoder destination is absent and has safe path components."""
    _walk_no_symlinks(path, allow_final_missing=True)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise MediaInspectError(f"output path is a symlink: {path}")
    raise MediaInspectError(f"output file already exists: {path}")


def _verify_created_file(path: Path, *, max_bytes: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    _walk_no_symlinks(path)
    descriptor, info = _open_regular(path)
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise MediaInspectError(f"derived file exceeds {max_bytes} bytes: {path.name}")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    if size < 1:
        raise MediaInspectError(f"derived file is empty: {path.name}")
    return {"path": path, "sha256": digest.hexdigest(), "size": size, "inode": info.st_ino}


def _safe_atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.name != RECEIPT_NAME:
        raise MediaInspectError("receipt must be named receipt.json")
    _safe_output_file(path)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise MediaInspectError("receipt exceeds size bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise MediaInspectError("receipt already exists") from exc
    except OSError as exc:
        raise MediaInspectError(f"cannot create receipt: {exc}") from exc
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _safe_replace_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically update a tool-owned receipt without following a link."""
    _walk_no_symlinks(path)
    descriptor, _ = _open_regular(path, flags=os.O_RDWR)
    os.close(descriptor)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise MediaInspectError("receipt exceeds size bound")
    try:
        _write_guard().atomic_write(path, raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise MediaInspectError(f"cannot update receipt safely: {exc}") from exc


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise MediaInspectError("artifact path escapes KB root") from exc


def _created_manifest(root: Path, entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for entry in entries:
        path = entry["path"]
        rel = _relative_path(root, path)
        if rel in seen:
            raise MediaInspectError("duplicate created artifact path")
        seen.add(rel)
        result.append({"path": rel, "sha256": entry["sha256"], "size": entry["size"]})
    result.sort(key=lambda item: item["path"])
    return result


def _list_tree(path: Path) -> list[Path]:
    """List every descendant and reject links/special files."""
    found: list[Path] = []
    _walk_no_symlinks(path)
    for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        # os.walk can race with a replacement; inspect every entry explicitly.
        for name in list(dirs):
            candidate = current_path / name
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                raise MediaInspectError(f"symlink in output tree: {candidate}")
            if not stat.S_ISDIR(info.st_mode):
                raise MediaInspectError(f"non-directory in output tree: {candidate}")
        for name in files:
            candidate = current_path / name
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                raise MediaInspectError(f"symlink in output tree: {candidate}")
            if not stat.S_ISREG(info.st_mode):
                raise MediaInspectError(f"special file in output tree: {candidate}")
            if info.st_nlink != 1:
                raise MediaInspectError(f"hard-linked output file: {candidate}")
            found.append(candidate)
    return found


def _frame_path(output_dir: Path, index: int, timestamp: float) -> Path:
    # Fixed decimal precision makes the evidence path itself timestamped while
    # remaining deterministic and safe for cleanup manifest matching.
    return output_dir / f"frame-{index:03d}-{timestamp:010.3f}s.jpg"


def extract_frame(
    input_path: Path,
    output_path: Path,
    timestamp: float,
    *,
    timeout: float,
) -> None:
    _safe_output_file(output_path)
    scale = (
        f"scale=w='min(iw,{MAX_FRAME_WIDTH})':h='min(ih,{MAX_FRAME_HEIGHT})':"
        "force_original_aspect_ratio=decrease"
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-ss", f"{timestamp:.6f}", "-i", str(input_path),
        "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
        "-vf", scale, "-q:v", "5", "-threads", "1", "-n", str(output_path),
    ]
    result = _run_process(command, timeout=timeout, label=f"ffmpeg frame {timestamp:.3f}s")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise MediaInspectError(
            f"ffmpeg frame {timestamp:.3f}s failed: {stderr[:500] or result.returncode}"
        )
    _verify_created_file(output_path)


def _asr_worker(audio_path: str, model_name: str, queue: Any) -> None:
    """Load exactly one local Whisper model and return timestamped segments."""
    try:
        # These environment switches are defense in depth around the explicit
        # ``local_files_only`` constructor argument; neither enables a network
        # fallback if a cache entry is absent.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        segments, info = model.transcribe(audio_path)
        result: list[dict[str, Any]] = []
        for segment in segments:
            # Keep malformed/empty segments in the private raw result.  The
            # parent process applies the deterministic quality gate; dropping
            # them here would hide exactly the output that needs review.
            raw_text = getattr(segment, "text", "")
            text = str(raw_text or "").strip()
            raw_start = getattr(segment, "start", None)
            raw_end = getattr(segment, "end", None)
            result.append({
                "start_seconds": _json_safe_asr_value(raw_start),
                "end_seconds": _json_safe_asr_value(raw_end),
                "text": text,
            })
        language = getattr(info, "language", None)
        queue.put({"segments": result, "language": language if isinstance(language, str) else None})
    except BaseException as exc:  # child errors are reported, not raised across process
        queue.put({"error": f"{type(exc).__name__}: {exc}"})


def _json_safe_asr_value(value: Any) -> Any:
    """Retain an ASR scalar while keeping the private JSON artifact valid."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else str(value)


def _asr_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normalise_asr_text(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold(), flags=re.UNICODE).strip()


def validate_asr_segments(
    segments: Any,
    *,
    duration_seconds: float,
    tolerance_seconds: float = ASR_TIMESTAMP_TOLERANCE_SECONDS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate cached-ASR timestamps/speech without interpreting content.

    The returned usable list is safe for a validated transcript artifact.  The
    quality record contains only segment indexes, counts, and deterministic
    reasons; raw speech stays in the private transcript artifact.
    """
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise MediaInspectError("ASR quality validation requires a finite positive clip duration")
    if (
        not math.isfinite(tolerance_seconds)
        or tolerance_seconds < 0
        or tolerance_seconds > ASR_TIMESTAMP_TOLERANCE_SECONDS
    ):
        raise MediaInspectError(
            "ASR timestamp tolerance must be between zero and "
            f"{ASR_TIMESTAMP_TOLERANCE_SECONDS:.0f}s"
        )

    raw_segments = segments if isinstance(segments, list) else []
    quality_reasons: list[str] = []
    excluded: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    usable_by_index: dict[int, dict[str, Any]] = {}
    repeated: defaultdict[str, list[int]] = defaultdict(list)
    upper_bound = duration_seconds + tolerance_seconds

    if not isinstance(segments, list):
        quality_reasons.append("ASR result did not contain a segment list")
    elif not segments:
        quality_reasons.append("ASR result contained no segments")

    for index, item in enumerate(raw_segments):
        reasons: list[str] = []
        if not isinstance(item, dict):
            reasons.append("segment is not an object")
        else:
            start_value = item.get("start_seconds")
            end_value = item.get("end_seconds")
            start = _asr_seconds(start_value)
            end = _asr_seconds(end_value)
            text = item.get("text")
            if start is None:
                reasons.append("start_seconds is not finite numeric data")
            if end is None:
                reasons.append("end_seconds is not finite numeric data")
            if start is not None and start < 0:
                reasons.append("start_seconds is before clip start")
            if end is not None and end > upper_bound:
                reasons.append(
                    f"end_seconds {end:.3f} is outside clip duration plus "
                    f"{tolerance_seconds:.3f}s tolerance"
                )
            if start is not None and end is not None and start >= end:
                reasons.append("start_seconds must be less than end_seconds")
            if not isinstance(text, str):
                reasons.append("text is not a string")
            else:
                clean_text = text.strip()
                if not clean_text or not any(character.isalnum() for character in clean_text):
                    reasons.append("text is empty or punctuation-only")

            if not reasons:
                # Keep a small canonical shape and avoid mutating engine data.
                clean_text = text.strip()
                usable_segment = {
                    "start_seconds": start,
                    "end_seconds": end,
                    "text": clean_text,
                }
                usable.append(usable_segment)
                usable_by_index[index] = usable_segment
                normalised = _normalise_asr_text(clean_text)
                if normalised:
                    repeated[normalised].append(index)

        if reasons:
            excluded.append({"index": index, "reasons": reasons})
            quality_reasons.extend(f"segment {index}: {reason}" for reason in reasons)

    warnings: list[dict[str, Any]] = []
    degenerate_indexes: set[int] = set()
    for normalised, indexes in sorted(repeated.items(), key=lambda item: item[1][0]):
        if len(indexes) >= 3:
            warnings.append({
                "kind": "repeated-content",
                "segment_indices": indexes,
                "occurrences": len(indexes),
                "message": (
                    "Repeated segment text is a warning only; repetition is not "
                    "treated as ground truth."
                ),
            })
        # A repeated phrase is not inherently bad (for example, a song may
        # repeat a lyric).  Exclude only the narrow failure shape exposed by
        # the pilot: at least three near/all-identical tiny one- or two-token
        # segments.  Normal multiword repeats remain warning-only.
        words = normalised.split()
        near_all = len(indexes) / len(usable) >= 0.8 if usable else False
        if (
            len(indexes) >= ASR_DEGENERATE_REPEAT_MIN_SEGMENTS
            and near_all
            and len(words) <= ASR_DEGENERATE_REPEAT_MAX_WORDS
            and len(normalised.replace(" ", "")) <= ASR_DEGENERATE_REPEAT_MAX_CHARS
        ):
            degenerate_indexes.update(indexes)

    for index in sorted(degenerate_indexes):
        reason = (
            "near/all-repeated tiny speech segments are excluded by the "
            "deterministic degenerate-repetition check; repeated speech may "
            "still be legitimate and requires review"
        )
        excluded.append({"index": index, "reasons": [reason]})
        quality_reasons.append(f"segment {index}: {reason}")
    if degenerate_indexes:
        usable = [
            segment for index, segment in usable_by_index.items()
            if index not in degenerate_indexes
        ]

    status = "valid" if usable and not excluded else ("partial" if usable else "unusable")
    quality = {
        "status": status,
        "duration_seconds": duration_seconds,
        "timestamp_tolerance_seconds": tolerance_seconds,
        "raw_segment_count": len(raw_segments),
        "usable_segment_count": len(usable),
        "excluded_segment_count": len(excluded),
        "excluded_segments": excluded,
        "reasons": quality_reasons,
        "warnings": warnings,
    }
    return usable, quality


def transcribe_offline(
    input_path: Path,
    *,
    model_name: str,
    timeout: float,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Run optional cached ASR with a process-level wall-clock bound."""
    context = mp.get_context("fork")
    queue = context.Queue(maxsize=1)
    process = context.Process(target=_asr_worker, args=(str(input_path), model_name, queue), daemon=True)
    process.start()
    result: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while process.is_alive() and result is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = queue.get(timeout=min(0.25, remaining))
        except Empty:
            continue
    if result is None and process.is_alive():
        process.terminate()
        process.join(2)
        queue.close()
        queue.join_thread()
        return "timeout", [], None
    if result is None:
        try:
            # A multiprocessing.Queue feeder can flush just after the child exits.
            result = queue.get(timeout=1.0)
        except Empty:
            queue.close()
            queue.join_thread()
            return "unavailable", [], None
    process.join(2)
    if process.is_alive():
        process.terminate()
        process.join(2)
    queue.close()
    queue.join_thread()
    if result.get("error"):
        return "unavailable", [], None
    segments = result.get("segments")
    if not isinstance(segments, list):
        return "unavailable", [], None
    return "ok", segments, result.get("language") if isinstance(result.get("language"), str) else None


def _ocr_failure(path: Path, timestamp: float, error: str, *, status: str = "failed") -> dict[str, Any]:
    """Return bounded per-frame OCR failure metadata without OCR payload."""
    return {
        "timestamp_seconds": timestamp,
        "frame": path.name,
        "status": status,
        "error": error[:500],
    }


def run_ocr(frame_paths: list[tuple[Path, float]], *, deadline: float) -> tuple[str, list[dict[str, Any]]]:
    if shutil.which("tesseract") is None:
        return "unavailable", []
    extracted: list[dict[str, Any]] = []
    successful = 0
    failed = 0
    recognized = 0
    for path, timestamp in frame_paths:
        try:
            result = _run_process(
                ["tesseract", str(path), "stdout", "--psm", "6"],
                timeout=_remaining(deadline),
                label=f"tesseract {path.name}",
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                detail = stderr[:500] or f"exit status {result.returncode}"
                extracted.append(_ocr_failure(path, timestamp, f"tesseract failed: {detail}"))
                failed += 1
                continue
            if len(result.stdout) > MAX_TEXT_BYTES:
                extracted.append(
                    _ocr_failure(path, timestamp, f"OCR output exceeds {MAX_TEXT_BYTES} bytes")
                )
                failed += 1
                continue
            text = result.stdout.decode("utf-8", errors="replace").strip()
            extracted.append({
                "timestamp_seconds": timestamp,
                "frame": path.name,
                "status": "ok",
                "text": text,
            })
            successful += 1
            if text:
                recognized += 1
        except MediaInspectTimeout as exc:
            extracted.append(_ocr_failure(path, timestamp, str(exc)))
            failed += 1
            # The shared deadline has expired.  Record the remaining frames as
            # not attempted instead of silently dropping them or restarting a
            # new timeout budget.
            for remaining_path, remaining_timestamp in frame_paths[len(extracted):]:
                extracted.append(
                    _ocr_failure(
                        remaining_path,
                        remaining_timestamp,
                        "OCR not attempted after the shared extraction timeout",
                        status="skipped",
                    )
                )
                failed += 1
            break
        except (MediaInspectError, OSError) as exc:
            extracted.append(_ocr_failure(path, timestamp, str(exc)))
            failed += 1

    if failed == 0:
        return ("ok" if recognized else "ok-no-text"), extracted
    if successful == 0:
        return "failed", extracted
    return "partial", extracted


def _write_json_artifact(path: Path, value: Any) -> dict[str, Any]:
    _safe_output_file(path)
    try:
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MediaInspectError(f"JSON extraction could not be serialized: {exc}") from exc
    if len(raw) > MAX_TEXT_BYTES:
        raise MediaInspectError(f"JSON extraction exceeds {MAX_TEXT_BYTES} bytes: {path.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise MediaInspectError(f"JSON artifact already exists: {path}") from exc
    except OSError as exc:
        raise MediaInspectError(f"cannot create JSON artifact {path}: {exc}") from exc
    try:
        offset = 0
        while offset < len(raw):
            try:
                written = os.write(descriptor, raw[offset:])
            except OSError as exc:
                raise MediaInspectError(f"cannot write JSON artifact {path}: {exc}") from exc
            if written <= 0:
                raise MediaInspectError(f"cannot write JSON artifact {path}: no progress")
            offset += written
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise MediaInspectError(f"cannot flush JSON artifact {path}: {exc}") from exc
    finally:
        os.close(descriptor)
    return _verify_created_file(path, max_bytes=MAX_TEXT_BYTES)


def _base_receipt(
    *,
    root: Path,
    origin_url: str,
    snapshot: FileSnapshot,
    kind: str,
    width: int | None,
    height: int | None,
    probe: VideoProbe | None,
    output_dir: Path,
    requested_frames: int,
    interval_seconds: float,
    asr_requested: bool,
    ocr_requested: bool,
    model_name: str,
    run_id: str,
    asset_id: str,
) -> dict[str, Any]:
    input_record: dict[str, Any] = {
        "path": _relative_path(root, snapshot.path),
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "kind": kind,
        "width": width,
        "height": height,
        "duration_seconds": probe.duration_seconds if probe else None,
        "has_audio": probe.has_audio if probe else False,
    }
    return {
        "author": "kb-bot",
        "version": 1,
        "tool": "kb-media-inspect",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "state": "INSPECTION_PARTIAL",
        "run_id": run_id,
        "asset_id": asset_id,
        "origin_url": origin_url,
        "input": input_record,
        # These top-level aliases keep the receipt easy to inspect in shell
        # tooling while the nested input record remains canonical.
        "input_sha256": snapshot.sha256,
        "input_duration_seconds": probe.duration_seconds if probe else None,
        "output_dir": _relative_path(root, output_dir),
        "engines": {
            "ffprobe": "local executable",
            "ffmpeg": "local executable" if probe else None,
            "ocr": "tesseract" if ocr_requested else None,
            "asr": {
                "model": model_name if asr_requested else None,
                "cache": "local_files_only" if asr_requested else None,
                "device": "cpu" if asr_requested else None,
                "compute_type": "int8" if asr_requested else None,
            },
        },
        "sampling": {
            "method": "regular-interval" if probe else "not-applicable",
            "requested_max_frames": requested_frames,
            "interval_seconds": interval_seconds if probe else None,
            "max_frame_width": MAX_FRAME_WIDTH,
            "max_frame_height": MAX_FRAME_HEIGHT,
            "timestamps_seconds": [],
            "frames_created": 0,
        },
        "asr": {
            "requested": asr_requested,
            "state": "not-requested" if not asr_requested else "pending",
            "model": model_name if asr_requested else None,
            "device": "cpu" if asr_requested else None,
            "compute_type": "int8" if asr_requested else None,
            "transcript_path": None,
            "transcript_status": None,
            "quality": {
                "status": "not-requested" if not asr_requested else "pending",
                "duration_seconds": probe.duration_seconds if probe else None,
                "timestamp_tolerance_seconds": ASR_TIMESTAMP_TOLERANCE_SECONDS,
                "raw_segment_count": 0,
                "usable_segment_count": 0,
                "excluded_segment_count": 0,
                "excluded_segments": [],
                "reasons": [],
                "warnings": [],
            },
        },
        "ocr": {
            "requested": ocr_requested,
            "state": "not-requested" if not ocr_requested else "pending",
            "path": None,
            "frames_succeeded": 0,
            "frames_failed": 0,
            "recognized_text_frames": 0,
            "failures": [],
        },
        "created_files": [],
        "coverage": {
            "status": "not-started",
            "covered": [],
            "omissions": [],
        },
        "cleanup": {
            "state": "not-requested",
            "required_disposition": None,
            "reviewed_disposition": None,
            "removed_files": [],
            "already_absent": [],
            "staged_input_deleted": False,
        },
        "evidence_limitations": [
            "Temporary local extraction only; no source publication or media-description note is created.",
            "No model interpretation is performed by this tool.",
            "The original staged input is not copied into the scratch output.",
        ],
        "sampling_limitations": [
            "Regularly sampled frames do not establish continuous visual coverage.",
            "Frame extraction and optional OCR may be incomplete if a decoder or timeout stops early.",
        ] if probe else ["Native view_image may inspect the original image; no derivative image is created."],
    }


def _append_limitation(receipt: dict[str, Any], value: str) -> None:
    values = receipt.setdefault("evidence_limitations", [])
    if value not in values:
        values.append(value)


def inspect_media(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = canonical_kb_root(args.kb_root)
    input_path = scoped_staged_input(args.input_path, root)
    output_dir = scoped_output_dir(args.output_dir, root)
    expected = args.input_sha256
    snapshot = read_snapshot(input_path, expected)
    origin_url = validate_origin_url(args.origin_url)
    kind = media_kind(input_path)
    if kind == "image" and (args.ocr or args.asr):
        raise MediaInspectError("--ocr and --asr are video-only extraction options; image inspection records custody only")
    timeout = float(args.timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > DEFAULT_TIMEOUT_SECONDS:
        raise MediaInspectError(f"timeout must be greater than zero and at most {DEFAULT_TIMEOUT_SECONDS:.0f}s")
    if type(args.max_frames) is not int or not 1 <= args.max_frames <= MAX_FRAMES:
        raise MediaInspectError(f"max frames must be an integer from 1 to {MAX_FRAMES}")
    interval = float(args.interval_seconds)
    if not math.isfinite(interval) or interval <= 0:
        raise MediaInspectError("sampling interval must be finite and greater than zero")

    probe: VideoProbe | None = None
    width: int | None = None
    height: int | None = None
    deadline = time.monotonic() + timeout
    if kind == "image":
        width, height = inspect_image(input_path)
    else:
        probe = probe_video(input_path, timeout=_remaining(deadline))
        width, height = probe.width, probe.height

    _mkdir_fresh(output_dir)
    receipt = _base_receipt(
        root=root,
        origin_url=origin_url,
        snapshot=snapshot,
        kind=kind,
        width=width,
        height=height,
        probe=probe,
        output_dir=output_dir,
        requested_frames=args.max_frames,
        interval_seconds=interval,
        asr_requested=bool(args.asr),
        ocr_requested=bool(args.ocr),
        model_name=args.whisper_model,
        run_id=args.run_id or output_dir.parent.name,
        asset_id=args.asset_id or input_path.stem,
    )
    created: list[dict[str, Any]] = []
    frame_paths: list[tuple[Path, float]] = []
    terminal_error: str | None = None
    asr_quality_failure = False
    timestamps: list[float] = []
    if probe is not None:
        receipt["coverage"]["covered"] = [
            "ffprobe video stream and finite duration",
            "regularly sampled timestamped frames",
        ]
        receipt["coverage"]["omissions"] = [
            "visual content between sampled timestamps",
            "continuous motion/audio synchronization",
        ]
        timestamps = regular_timestamps(probe.duration_seconds, args.max_frames, interval)
        receipt["sampling"]["timestamps_seconds"] = timestamps
        for index, timestamp in enumerate(timestamps, start=1):
            path = _frame_path(output_dir, index, timestamp)
            try:
                extract_frame(input_path, path, timestamp, timeout=_remaining(deadline))
                entry = _verify_created_file(path)
                created.append(entry)
                frame_paths.append((path, timestamp))
            except MediaInspectTimeout as exc:
                terminal_error = str(exc)
                _append_limitation(receipt, terminal_error)
                break
            except MediaInspectError as exc:
                terminal_error = str(exc)
                _append_limitation(receipt, terminal_error)
                break

        receipt["sampling"]["frames_created"] = len(frame_paths)
        if args.ocr and frame_paths:
            try:
                state, ocr_items = run_ocr(frame_paths, deadline=deadline)
                receipt["ocr"]["state"] = state
                successful_items = [item for item in ocr_items if item.get("status") == "ok"]
                failed_items = [
                    item for item in ocr_items if item.get("status") in {"failed", "skipped"}
                ]
                receipt["ocr"]["frames_succeeded"] = len(successful_items)
                receipt["ocr"]["frames_failed"] = len(failed_items)
                receipt["ocr"]["recognized_text_frames"] = sum(
                    1 for item in successful_items if isinstance(item.get("text"), str) and item["text"]
                )
                receipt["ocr"]["failures"] = [
                    {
                        "timestamp_seconds": item.get("timestamp_seconds"),
                        "frame": item.get("frame"),
                        "status": item.get("status"),
                        "error": str(item.get("error", ""))[:500],
                    }
                    for item in ocr_items
                    if item.get("status") in {"failed", "skipped"}
                ]
                if state in {"ok", "ok-no-text", "partial", "failed"}:
                    ocr_entry = _write_json_artifact(output_dir / "ocr.json", {
                        "author": "kb-bot",
                        "version": 1,
                        "frames": ocr_items,
                        "limitations": [
                            "OCR output is extraction evidence, not visual interpretation.",
                            "Each frame entry records whether OCR succeeded, failed, or was skipped.",
                        ],
                    })
                    created.append(ocr_entry)
                    receipt["ocr"]["path"] = _relative_path(root, output_dir / "ocr.json")
                if state in {"partial", "failed"}:
                    terminal_error = (
                        f"OCR {state}: {len(failed_items)} of {len(frame_paths)} frame(s) failed"
                    )
                    _append_limitation(receipt, terminal_error)
                elif state == "unavailable":
                    terminal_error = f"OCR is {state}"
                    _append_limitation(receipt, terminal_error)
                elif state == "ok-no-text":
                    _append_limitation(receipt, "OCR completed successfully but recognized no text in any frame.")
            except (MediaInspectError, OSError) as exc:
                receipt["ocr"]["state"] = "failed"
                terminal_error = str(exc)
                _append_limitation(receipt, str(exc))
        elif args.ocr:
            receipt["ocr"]["state"] = "not-run-no-frames"

        if args.asr:
            if not probe.has_audio:
                receipt["asr"]["state"] = "skipped-no-audio"
                receipt["asr"]["quality"]["status"] = "not-applicable"
                receipt["coverage"]["omissions"].append("audio transcript: no audio stream")
                _append_limitation(receipt, "No audio stream was reported by ffprobe; no transcript was fabricated.")
            elif terminal_error is not None:
                receipt["asr"]["state"] = "skipped-after-extraction-error"
                receipt["asr"]["quality"]["status"] = "not-run"
            else:
                try:
                    state, segments, language = transcribe_offline(
                        input_path,
                        model_name=args.whisper_model,
                        timeout=_remaining(deadline),
                    )
                    receipt["asr"]["language"] = language
                    if state == "ok":
                        usable_segments, quality = validate_asr_segments(
                            segments,
                            duration_seconds=probe.duration_seconds,
                        )
                        receipt["asr"]["quality"] = quality
                        if quality["status"] == "valid":
                            transcript_payload = {
                                "author": "kb-bot",
                                "version": 1,
                                "model": args.whisper_model,
                                "device": "cpu",
                                "compute_type": "int8",
                                "segments": usable_segments,
                                "validation": quality,
                                "limitations": [
                                    "Offline ASR segments reflect the cached model output and are not a source-truth claim.",
                                ],
                            }
                            receipt["asr"]["state"] = "ok"
                            receipt["asr"]["transcript_status"] = "validated"
                        else:
                            asr_quality_failure = True
                            transcript_payload = {
                                "author": "kb-bot",
                                "version": 1,
                                "model": args.whisper_model,
                                "device": "cpu",
                                "compute_type": "int8",
                                "raw_segments": segments,
                                "validated_segments": usable_segments,
                                "validation": quality,
                                "raw_output_status": "unvalidated-excluded",
                                "limitations": [
                                    "Raw engine output is private scratch evidence, unvalidated, and excluded from KB/source use.",
                                    "Validated segments use only deterministic timestamp and speech-presence checks; no content interpretation was performed.",
                                ],
                            }
                            receipt["asr"]["state"] = quality["status"]
                            receipt["asr"]["transcript_status"] = "unvalidated-excluded"
                            detail = quality["reasons"][0] if quality["reasons"] else "no usable segments"
                            terminal_error = (
                                f"Offline ASR quality validation is {quality['status']}: {detail}"
                            )
                            _append_limitation(receipt, terminal_error)
                        transcript_entry = _write_json_artifact(output_dir / "transcript.json", transcript_payload)
                        created.append(transcript_entry)
                        receipt["asr"]["transcript_path"] = _relative_path(root, output_dir / "transcript.json")
                    elif state == "timeout":
                        receipt["asr"]["state"] = state
                        receipt["asr"]["quality"]["status"] = "not-run-timeout"
                        terminal_error = "Offline ASR exceeded the shared extraction timeout."
                        _append_limitation(receipt, "Offline ASR exceeded the shared extraction timeout.")
                    else:
                        receipt["asr"]["state"] = state
                        receipt["asr"]["quality"]["status"] = "not-available"
                        terminal_error = (
                            "Offline ASR was unavailable from the existing local cache; "
                            "no transcript was fabricated."
                        )
                        _append_limitation(
                            receipt,
                            "Offline ASR was unavailable from the existing local cache; "
                            "no transcript was fabricated.",
                        )
                except (MediaInspectError, OSError) as exc:
                    receipt["asr"]["state"] = "failed"
                    asr_quality_failure = False
                    terminal_error = str(exc)
                    _append_limitation(receipt, str(exc))

    try:
        verify_snapshot_unchanged(snapshot)
    except MediaInspectError as exc:
        asr_quality_failure = False
        terminal_error = str(exc)
        _append_limitation(receipt, terminal_error)

    if probe is None:
        receipt["coverage"] = {
            "status": "complete",
            "covered": ["hash-verified staged image bytes and dimensions"],
            "omissions": ["no derivative image; native image viewing is a separate inspection step"],
        }
    else:
        receipt["coverage"]["status"] = "partial"
        if len(frame_paths) == len(timestamps):
            receipt["coverage"]["covered"].append("all requested frame timestamps decoded")
        else:
            receipt["coverage"]["omissions"].append("one or more requested frame timestamps did not decode")

    # A fresh output directory should contain only files tracked by this
    # helper.  Stop before receipt publication if another writer introduced an
    # extra file, and do not silently claim cleanup authority over it.
    try:
        actual_files = set(_list_tree(output_dir))
    except MediaInspectError as exc:
        asr_quality_failure = False
        terminal_error = str(exc)
        actual_files = set()
        _append_limitation(receipt, terminal_error)
    tracked_files = {entry["path"] for entry in created}
    extras = actual_files - tracked_files
    if extras:
        asr_quality_failure = False
        terminal_error = "unexpected files in fresh output directory: " + ", ".join(sorted(p.name for p in extras))
        _append_limitation(receipt, terminal_error)

    if asr_quality_failure and terminal_error is not None:
        receipt["cleanup"]["required_disposition"] = ASR_REVIEWED_DISPOSITION

    receipt["created_files"] = _created_manifest(root, created)
    receipt["state"] = "INSPECTION_FAILED" if terminal_error and not created else (
        "INSPECTION_PARTIAL" if terminal_error else "INSPECTION_COMPLETE"
    )
    if receipt["state"] == "INSPECTION_PARTIAL" and terminal_error:
        receipt["error"] = terminal_error
    receipt_path = output_dir / RECEIPT_NAME
    _safe_atomic_json(receipt_path, receipt)
    result = {
        "state": receipt["state"],
        "receipt": _relative_path(root, receipt_path),
        "created_files": receipt["created_files"],
        "input_sha256": snapshot.sha256,
        "output_dir": _relative_path(root, output_dir),
    }
    if terminal_error:
        result["error"] = terminal_error
    return result, 0 if receipt["state"] == "INSPECTION_COMPLETE" else 2


def _strict_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RECEIPT_BYTES:
        raise MediaInspectError("receipt exceeds size bound")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise MediaInspectError(f"duplicate receipt field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise MediaInspectError("invalid receipt JSON") from exc
    if not isinstance(value, dict):
        raise MediaInspectError("receipt JSON root must be an object")
    return value


def _quality_partial_is_reviewable(receipt: dict[str, Any]) -> bool:
    """Recognize only a completed-frame ASR-quality partial for opt-in cleanup."""
    cleanup = receipt.get("cleanup")
    asr = receipt.get("asr")
    quality = asr.get("quality") if isinstance(asr, dict) else None
    sampling = receipt.get("sampling")
    coverage = receipt.get("coverage")
    if not isinstance(cleanup, dict) or not isinstance(asr, dict) or not isinstance(quality, dict):
        return False
    if not isinstance(sampling, dict) or not isinstance(coverage, dict):
        return False
    timestamps = sampling.get("timestamps_seconds")
    frames_created = sampling.get("frames_created")
    transcript_path = asr.get("transcript_path")
    created_files = receipt.get("created_files")
    error = receipt.get("error")
    return (
        receipt.get("state") == "INSPECTION_PARTIAL"
        and cleanup.get("required_disposition") == ASR_REVIEWED_DISPOSITION
        and asr.get("state") in {"partial", "unusable"}
        and asr.get("transcript_status") == "unvalidated-excluded"
        and quality.get("status") in {"partial", "unusable"}
        and isinstance(transcript_path, str)
        and isinstance(created_files, list)
        and any(
            isinstance(entry, dict) and entry.get("path") == transcript_path
            for entry in created_files
        )
        and coverage.get("status") == "partial"
        and isinstance(timestamps, list)
        and type(frames_created) is int
        and frames_created > 0
        and frames_created == len(timestamps)
        and isinstance(error, str)
        and error.startswith("Offline ASR quality validation is ")
    )


def _read_receipt(
    path: Path,
    root: Path,
    *,
    allow_reviewed_quality: bool = False,
) -> dict[str, Any]:
    path = scoped_path(path, root)
    scratch = staging_root(root)
    if not _under(path, scratch, strict=True) or path.name != RECEIPT_NAME:
        raise MediaInspectError("cleanup receipt must be receipt.json under KB media staging")
    descriptor, _ = _open_regular(path)
    try:
        raw = os.read(descriptor, MAX_RECEIPT_BYTES + 1)
    finally:
        os.close(descriptor)
    receipt = _strict_json(raw)
    if receipt.get("author") != "kb-bot" or receipt.get("version") != 1:
        raise MediaInspectError("receipt ownership/version is invalid")
    if receipt.get("tool") != "kb-media-inspect":
        raise MediaInspectError("receipt tool identifier is invalid")
    if receipt.get("state") not in {"INSPECTION_COMPLETE", "CLEANUP_COMPLETE"} and not (
        allow_reviewed_quality and _quality_partial_is_reviewable(receipt)
    ):
        raise MediaInspectError("cleanup refused: inspection receipt is incomplete")
    if not isinstance(receipt.get("input"), dict) or not isinstance(receipt.get("created_files"), list):
        raise MediaInspectError("receipt lacks input or created_files manifest")
    output_dir = receipt.get("output_dir")
    if not isinstance(output_dir, str):
        raise MediaInspectError("receipt output_dir is invalid")
    output = scoped_output_existing(output_dir, root)
    if output != path.parent:
        raise MediaInspectError("receipt output_dir does not match receipt location")
    return receipt


def scoped_output_existing(value: Path | str, root: Path) -> Path:
    path = scoped_path(value, root)
    scratch = staging_root(root)
    if not _under(path, scratch, strict=True) or path == scratch:
        raise MediaInspectError("receipt output directory is outside private staging")
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MediaInspectError("receipt output directory must be a real directory")
    return path


def _manifest_paths(receipt: dict[str, Any], root: Path, output_dir: Path) -> dict[Path, dict[str, Any]]:
    entries = receipt["created_files"]
    result: dict[Path, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise MediaInspectError("receipt created_files entry has invalid fields")
        rel = entry["path"]
        digest = entry["sha256"]
        size = entry["size"]
        if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
            raise MediaInspectError("receipt artifact path must be KB-relative")
        if not SHA256_RE.fullmatch(digest) or type(size) is not int or not 1 <= size <= MAX_FRAME_BYTES:
            raise MediaInspectError("receipt artifact hash/size is invalid")
        path = scoped_path(rel, root, allow_missing=True)
        if path.parent != output_dir and not _under(path, output_dir, strict=True):
            raise MediaInspectError("receipt artifact escapes output directory")
        if path.name == RECEIPT_NAME and path.parent == output_dir:
            raise MediaInspectError("receipt itself may not be listed for cleanup")
        if path in result:
            raise MediaInspectError("duplicate artifact in receipt manifest")
        result[path] = {"sha256": digest, "size": size}
    return result


def _verify_manifest_file(path: Path, expected: dict[str, Any], *, allow_missing: bool = True) -> str:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return "already-absent"
        raise MediaInspectError(f"manifest artifact is missing: {path}")
    if stat.S_ISLNK(info.st_mode):
        raise MediaInspectError(f"manifest artifact is a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise MediaInspectError(f"manifest artifact is not regular: {path}")
    if info.st_nlink != 1:
        raise MediaInspectError(f"manifest artifact is hard-linked: {path}")
    if info.st_size != expected["size"]:
        raise MediaInspectError(f"manifest artifact size changed: {path}")
    checked = _verify_created_file(path, max_bytes=max(MAX_FRAME_BYTES, expected["size"]))
    if checked["sha256"] != expected["sha256"]:
        raise MediaInspectError(f"manifest artifact hash changed: {path}")
    return "present"


def _checked_review_ref(value: Path | str, root: Path) -> str:
    """Validate an existing KB-relative review reference without changing it."""
    path = scoped_path(value, root)
    descriptor, _ = _open_regular(path)
    os.close(descriptor)
    return _relative_path(root, path)


def cleanup_media(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = canonical_kb_root(args.kb_root)
    receipt_path = scoped_path(args.receipt, root)
    reviewed_disposition = getattr(args, "reviewed_disposition", None)
    review_ref_value = getattr(args, "review_ref", None)
    if reviewed_disposition not in {None, ASR_REVIEWED_DISPOSITION}:
        raise MediaInspectError("reviewed disposition is not supported")
    if (reviewed_disposition is None) != (review_ref_value is None):
        raise MediaInspectError(
            "--reviewed-disposition unusable-asr requires an existing --review-ref"
        )
    receipt = _read_receipt(
        receipt_path,
        root,
        allow_reviewed_quality=(reviewed_disposition == ASR_REVIEWED_DISPOSITION),
    )
    output_dir = receipt_path.parent
    prior_cleanup = receipt.get("cleanup") if isinstance(receipt.get("cleanup"), dict) else {}
    if receipt.get("state") == "INSPECTION_COMPLETE" and reviewed_disposition is not None:
        raise MediaInspectError("reviewed disposition applies only to unusable-ASR partial receipts")
    if receipt.get("state") == "CLEANUP_COMPLETE" and reviewed_disposition is not None:
        if (
            prior_cleanup.get("reviewed_disposition") != reviewed_disposition
            or prior_cleanup.get("review_ref") != _relative_path(root, scoped_path(review_ref_value, root))
        ):
            raise MediaInspectError("reviewed disposition does not match completed cleanup")
    review_ref = None
    if review_ref_value is not None:
        review_ref = _checked_review_ref(review_ref_value, root)
        review_path = root / Path(review_ref)
        if _under(review_path, staging_root(root), strict=True):
            raise MediaInspectError("review reference may not point into media inspection scratch")
    if receipt.get("state") == "INSPECTION_PARTIAL" and reviewed_disposition != ASR_REVIEWED_DISPOSITION:
        raise MediaInspectError("cleanup refused: inspection receipt is incomplete")
    manifest = _manifest_paths(receipt, root, output_dir)
    actual_files = _list_tree(output_dir)
    allowed = set(manifest)
    extras = set(actual_files) - allowed - {receipt_path}
    if extras:
        raise MediaInspectError(
            "cleanup refused: extra or unlisted files present: "
            + ", ".join(sorted(p.name for p in extras))
        )

    statuses: dict[Path, str] = {}
    for path, expected in manifest.items():
        statuses[path] = _verify_manifest_file(path, expected)

    delete_input = args.delete_staged_input
    input_path: Path | None = None
    if delete_input is not None:
        input_path = scoped_path(delete_input, root, allow_missing=True)
        scratch = staging_root(root)
        if not _under(input_path, scratch, strict=True):
            raise MediaInspectError("named staged input must be under KB _tools/staging/media/")
        if input_path == output_dir or _under(input_path, output_dir, strict=True):
            raise MediaInspectError("named staged input may not be inside output directory")
        input_record = receipt["input"]
        if input_record.get("path") != _relative_path(root, input_path):
            raise MediaInspectError("named staged input does not match receipt input")
        expected_hash = input_record.get("sha256")
        if not isinstance(expected_hash, str):
            raise MediaInspectError("receipt input hash is invalid")
        try:
            read_snapshot(input_path, expected_hash)
        except MediaInspectMissing:
            # A repeated cleanup is allowed to confirm that the explicitly
            # named input was already removed; changed bytes are not.
            input_path = None

    removed: list[str] = []
    already_absent: list[str] = []
    for path, status in statuses.items():
        if status == "already-absent":
            already_absent.append(_relative_path(root, path))
            continue
        # Recheck immediately before unlinking.  Cleanup never follows links
        # and never recursively removes a directory.
        _verify_manifest_file(path, manifest[path], allow_missing=False)
        try:
            os.unlink(path)
        except OSError as exc:
            raise MediaInspectError(f"cleanup could not remove exact artifact {path}: {exc}") from exc
        removed.append(_relative_path(root, path))

    input_deleted = False
    if input_path is not None:
        # Hash verification above is repeated at the point of deletion so a
        # concurrent replacement cannot turn an approved input-delete into a
        # broad or stale deletion.
        input_record = receipt["input"]
        try:
            read_snapshot(input_path, input_record["sha256"])
        except MediaInspectMissing:
            # If another same-user cleanup removed the exact reviewed input in
            # the small check/unlink window, preserve idempotency and do not
            # claim that this invocation deleted it.
            input_path = None
        if input_path is None:
            input_deleted = False
        else:
            try:
                os.unlink(input_path)
            except OSError as exc:
                raise MediaInspectError(f"cleanup could not remove named staged input: {exc}") from exc
            input_deleted = True

    cleanup_disposition = {
        "state": "complete",
        "required_disposition": prior_cleanup.get("required_disposition"),
        "reviewed_disposition": reviewed_disposition or prior_cleanup.get("reviewed_disposition"),
        "review_ref": review_ref or prior_cleanup.get("review_ref"),
        "removed_files": sorted(removed),
        "already_absent": sorted(already_absent),
        "staged_input_deleted": input_deleted,
        "staged_input_already_absent": delete_input is not None and input_path is None,
    }
    receipt["cleanup"] = cleanup_disposition
    receipt["state"] = "CLEANUP_COMPLETE"
    _safe_replace_json(receipt_path, receipt)

    result = {
        "state": "CLEANUP_COMPLETE",
        "receipt": _relative_path(root, receipt_path),
        "reviewed_disposition": cleanup_disposition["reviewed_disposition"],
        "review_ref": cleanup_disposition["review_ref"],
        "removed_files": sorted(removed),
        "already_absent": sorted(already_absent),
        "staged_input_deleted": input_deleted,
        "staged_input_already_absent": delete_input is not None and input_path is None,
    }
    return result, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", dest="input_path", type=Path, help="exact staged image/video path")
    mode.add_argument("--cleanup-receipt", dest="receipt", type=Path, help="receipt.json to use for exact cleanup")
    parser.add_argument(
        "--input-sha256", "--sha256", "--expected-sha256",
        help="full lowercase SHA-256 of the staged input",
    )
    parser.add_argument(
        "--origin-url", "--origin",
        help="canonical source URL recorded as provenance only",
    )
    parser.add_argument(
        "--output-dir", "--out-dir", type=Path,
        help="fresh child directory under _tools/staging/media/",
    )
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--timeout-seconds", "--timeout", type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--run-id", help="operation/run identifier stored in the receipt")
    parser.add_argument("--asset-id", help="asset identifier stored in the receipt")
    parser.add_argument(
        "--asr", action="store_true",
        help="optional cached faster-whisper ASR; local_files_only, CPU int8",
    )
    parser.add_argument("--ocr", action="store_true", help="optional local Tesseract OCR over extracted frames")
    parser.add_argument("--whisper-model", default="Systran/faster-whisper-base")
    parser.add_argument(
        "--delete-staged-input", "--staged-input", dest="delete_staged_input", type=Path,
        help="cleanup only: explicitly name the already-reviewed staged input to delete",
    )
    parser.add_argument(
        "--reviewed-disposition",
        choices=(ASR_REVIEWED_DISPOSITION,),
        help="cleanup only: authorize removal of an unusable-ASR completed extraction",
    )
    parser.add_argument(
        "--review-ref", type=Path,
        help="cleanup only: existing KB-relative independent review record",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.input_path is not None:
            if args.input_sha256 is None or args.origin_url is None or args.output_dir is None:
                parser.error("--input requires --input-sha256, --origin-url and --output-dir")
            if args.delete_staged_input is not None:
                parser.error("--delete-staged-input is cleanup-only")
            if args.reviewed_disposition is not None or args.review_ref is not None:
                parser.error("review options are cleanup-only")
            result, code = inspect_media(args)
        else:
            if (
                args.input_sha256 is not None
                or args.origin_url is not None
                or args.output_dir is not None
                or args.run_id is not None
                or args.asset_id is not None
                or args.asr
                or args.ocr
                or args.whisper_model != "Systran/faster-whisper-base"
            ):
                parser.error("inspection-only options cannot be combined with --cleanup-receipt")
            if (args.reviewed_disposition is None) != (args.review_ref is None):
                parser.error("--reviewed-disposition unusable-asr requires --review-ref")
            result, code = cleanup_media(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return code
    except (MediaInspectError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"state": "STOP", "error": str(exc)}, ensure_ascii=False, sort_keys=True, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
