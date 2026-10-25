#!/usr/bin/env python3
"""Historical video helpers; the unbounded URL-analysis entrypoint is retired."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FRAME_INTERVAL_SECONDS = 2.0
DEDUP_DISTANCE = 6
OCR_FRAME_TIMEOUT_SECONDS = 90
RETIRED_INTERPRETATION_MESSAGE = (
    "extract transcript/frames, then delegate interpretation to the active native KB worker "
    "(no CLI simulation)."
)
RETIRED_VIDEO_ROUTE_MESSAGE = (
    "The unbounded URL/video analyzer is retired. Stage exact approved assets with "
    "kb-media-capture.py --temporary-inspection, then use the local-only "
    "kb-media-inspect.py. No download, model call or output was started."
)
EVIDENCE_QUALITY_VALUES = frozenset(
    {
        "transcript-plus-visual",
        "transcript-only",
        "visual-only",
        "summary-only",
        "insufficient",
    }
)


class RetiredInterpretationRouteError(RuntimeError):
    """Raised when a retired provider-backed interpretation route is requested."""


class RetiredVideoRouteError(RuntimeError):
    """The historical URL downloader is not an executable KB route."""


DEFAULT_KEYFRAME_TERMS = (
    "chart",
    "graph",
    "diagram",
    "figure",
    "table",
    "slide",
    "screen",
    "screenshot",
    "demo",
    "dashboard",
    "code",
    "terminal",
    "equation",
    "formula",
    "plot",
    "map",
)


def warn(message: str) -> None:
    print(f"kb-video-analyze: {message}", file=sys.stderr)


def dump(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a video URL for KB ingestion.")
    parser.add_argument("url")
    parser.add_argument("--out", help="Markdown artifact path. Defaults to <workdir>/analysis.md.")
    parser.add_argument("--json", action="store_true", help="Print the structured artifact JSON to stdout.")
    parser.add_argument("--workdir", help="Working directory for downloads and keyframes. Defaults to a tempdir.")
    parser.add_argument("--transcript-only", action="store_true", help="Skip frame sampling, OCR, and vision.")
    parser.add_argument("--no-ocr", action="store_true", help="Sample keyframes without OCR.")
    parser.add_argument(
        "--ocr-engine",
        choices=["unlimited", "auto", "unlimited-hf", "tesseract", "mistral"],
        default=None,
        help="Explicit keyframe OCR engine; omit to skip OCR. "
        "Available engines depend on the installed OCR helper.",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="Retired compatibility flag; extract frames and delegate interpretation to the active native KB worker.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Retired compatibility flag; extract the transcript and delegate interpretation to the active native KB worker.",
    )
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="faster-whisper compute type. Default 'int8' is much faster on CPU than float32 fallback.",
    )
    parser.add_argument(
        "--frame-timestamps",
        help="Comma-separated timestamps (seconds or HH:MM:SS/MM:SS) to extract before falling back to visual sampling.",
    )
    parser.add_argument(
        "--focus-terms",
        help="Comma-separated transcript terms to prioritize for keyframe timestamps. "
        "Used in addition to the default visual/demo terms when --frame-timestamps is omitted.",
    )
    parser.add_argument("--max-frames", type=int, default=40)
    return parser


def maybe_reexec_tools_venv() -> None:
    """Use the KB tools venv when the script is launched directly from system Python."""
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if not venv_python.exists() or Path(sys.prefix).resolve() == venv_python.parents[1].resolve():
        return
    try:
        import yt_dlp  # noqa: F401
        import faster_whisper  # noqa: F401
    except Exception:
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def reject_retired_interpretation_flags(args: argparse.Namespace) -> None:
    """Fail before any setup or extraction when a retired route is requested."""
    retired = [
        flag
        for flag, enabled in (
            ("--vision", getattr(args, "vision", False)),
            ("--summary", getattr(args, "summary", False)),
        )
        if enabled
    ]
    if retired:
        flags = ", ".join(retired)
        raise RetiredInterpretationRouteError(f"{flags} is retired; {RETIRED_INTERPRETATION_MESSAGE}")


def parse_timestamp(value: str) -> float:
    value = value.strip()
    if not value:
        raise ValueError("empty timestamp")
    if ":" not in value:
        return float(value)
    parts = [float(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"unsupported timestamp: {value}")


def parse_timestamps(raw: str | None) -> list[float]:
    if not raw:
        return []
    return [parse_timestamp(item) for item in raw.split(",") if item.strip()]


def parse_focus_terms(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass
class DownloadedMedia:
    metadata: dict[str, Any]
    audio_path: Path | None = None
    video_path: Path | None = None


def extract_metadata(info: dict[str, Any], url: str) -> dict[str, Any]:
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration"),
        "id": info.get("id"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "upload_date": info.get("upload_date"),
    }


def yt_dlp_extract(url: str, workdir: Path, need_audio: bool, need_video: bool) -> DownloadedMedia:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        warn(f"yt-dlp unavailable: {exc}")
        return DownloadedMedia(metadata={"url": url})

    info: dict[str, Any] = {}
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            raw = ydl.extract_info(url, download=False)
        if isinstance(raw, dict):
            info = raw
    except Exception as exc:
        warn(f"yt-dlp metadata fetch failed: {exc}")

    metadata = extract_metadata(info, url) if info else {"url": url}
    audio_path = download_with_yt_dlp(url, workdir, "audio", "bestaudio/best") if need_audio else None
    video_path = download_with_yt_dlp(url, workdir, "video", "best[ext=mp4]/best") if need_video else None
    return DownloadedMedia(metadata=metadata, audio_path=audio_path, video_path=video_path)


def download_with_yt_dlp(url: str, workdir: Path, stem: str, fmt: str) -> Path | None:
    try:
        import yt_dlp  # type: ignore
    except Exception:
        return None
    outtmpl = str(workdir / f"{stem}.%(ext)s")
    opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        warn(f"yt-dlp {stem} download failed: {exc}")
        return None
    matches = sorted(workdir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def youtube_video_id(url: str, metadata: dict[str, Any]) -> str | None:
    extractor = str(metadata.get("extractor") or "").lower()
    if "youtube" in extractor and metadata.get("id"):
        return str(metadata["id"])
    patterns = (
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{6,})",
    )
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def normalize_caption_entries(raw_entries: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in raw_entries or []:
        if isinstance(item, dict):
            text_value = item.get("text")
            start_value = item.get("start")
            duration_value = item.get("duration")
        else:
            text_value = getattr(item, "text", None)
            start_value = getattr(item, "start", None)
            duration_value = getattr(item, "duration", None)
        text = str(text_value or "").replace("\n", " ").strip()
        if not text:
            continue
        start = float(start_value or 0.0)
        duration = float(duration_value or 0.0)
        entries.append({"start": start, "end": start + duration, "text": text, "source": "youtube-captions"})
    return entries


def fetch_youtube_captions(url: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    video_id = youtube_video_id(url, metadata)
    if not video_id:
        return []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except Exception as exc:
        warn(f"youtube-transcript-api unavailable: {exc}")
        return []
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):
            return normalize_caption_entries(api.fetch(video_id))
        return normalize_caption_entries(YouTubeTranscriptApi.get_transcript(video_id))
    except Exception as exc:
        warn(f"caption fetch failed: {exc}")
        return []


def transcribe_with_whisper(audio_path: Path | None, model_name: str, device: str, compute_type: str) -> list[dict[str, Any]]:
    if audio_path is None:
        warn("ASR skipped: no audio download available")
        return []
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        warn(f"faster-whisper unavailable: {exc}")
        return []
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(str(audio_path))
        return [
            {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(), "source": "faster-whisper"}
            for seg in segments
            if seg.text.strip()
        ]
    except Exception as exc:
        warn(f"ASR failed: {exc}")
        return []


def get_transcript_with_fallback(
    url: str,
    workdir: Path,
    media: DownloadedMedia,
    model_name: str,
    device: str,
    compute_type: str,
) -> list[dict[str, Any]]:
    captions = fetch_youtube_captions(url, media.metadata)
    if captions:
        return captions
    if media.audio_path is None:
        media.audio_path = download_with_yt_dlp(url, workdir, "audio", "bestaudio/best")
    return transcribe_with_whisper(media.audio_path, model_name, device, compute_type)


def frame_hash(image: Any) -> Any:
    try:
        from PIL import Image  # type: ignore
        import imagehash  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"imagehash/Pillow unavailable: {exc}") from exc

    if image.__class__.__module__.startswith("PIL."):
        pil_image = image
    else:
        try:
            import cv2  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"cv2 unavailable: {exc}") from exc
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    return imagehash.phash(pil_image)


def is_distinct_hash(candidate: Any, seen: list[Any], threshold: int = DEDUP_DISTANCE) -> bool:
    return all(candidate - old >= threshold for old in seen)


_OCR_ENGINES = None


def _load_ocr_engines() -> Any:
    """Import kb-ocr-enrich.py's engine functions (stdlib-only) for keyframe OCR."""
    global _OCR_ENGINES
    if _OCR_ENGINES is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "kb-ocr-enrich.py"
        spec = importlib.util.spec_from_file_location("kb_ocr_enrich", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _OCR_ENGINES = mod
    return _OCR_ENGINES


def _ocr_frame_tesseract(path: Path) -> str:
    if shutil.which("tesseract") is None:
        warn("OCR skipped: tesseract not found")
        return ""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception as exc:
        warn(f"OCR dependencies unavailable: {exc}")
        return ""
    try:
        return pytesseract.image_to_string(Image.open(path)).strip()
    except Exception as exc:
        warn(f"OCR failed for {path}: {exc}")
        return ""


def _ocr_frame_worker(path: str, engine: str, result_path: str) -> None:
    try:
        if engine == "tesseract":
            Path(result_path).write_text(_ocr_frame_tesseract(Path(path)), encoding="utf-8")
            return
        eng = _load_ocr_engines()
        fn = {
            "unlimited": eng.run_unlimited_ocr,
            "unlimited-hf": eng.run_unlimited_hf_ocr,
            "mistral": eng.run_mistral_ocr,
            "auto": eng.run_auto_ocr,
        }[engine]
        Path(result_path).write_text(fn(Path(path)).strip(), encoding="utf-8")
    except Exception as exc:
        Path(result_path).write_text(f"__KB_OCR_ERROR__{exc}", encoding="utf-8")


def ocr_frame(path: Path, engine: str | None = None, timeout: int = OCR_FRAME_TIMEOUT_SECONDS) -> str:
    if not engine:
        warn("OCR skipped: no OCR engine selected")
        return ""
    if engine == "tesseract":
        return _ocr_frame_tesseract(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="kb-video-ocr-", suffix=".txt", delete=False) as tmp:
        result_path = Path(tmp.name)
    try:
        proc = mp.Process(target=_ocr_frame_worker, args=(str(path), engine, str(result_path)))
        proc.start()
        proc.join(timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            warn(f"OCR ({engine}) timed out after {timeout}s for {path}; falling back to tesseract")
            return _ocr_frame_tesseract(path)
        result = result_path.read_text(encoding="utf-8", errors="replace")
        if not result:
            warn(f"OCR ({engine}) returned no result for {path}; falling back to tesseract")
            return _ocr_frame_tesseract(path)
        if result.startswith("__KB_OCR_ERROR__"):
            warn(f"OCR ({engine}) failed for {path}: {result.removeprefix('__KB_OCR_ERROR__')}; falling back to tesseract")
            return _ocr_frame_tesseract(path)
        return result.strip()
    finally:
        try:
            result_path.unlink()
        except OSError:
            pass


def sample_keyframes(video_path: Path | None, workdir: Path, max_frames: int, do_ocr: bool, ocr_engine: str | None = None) -> list[dict[str, Any]]:
    if video_path is None:
        warn("frame sampling skipped: no video download available")
        return []
    try:
        import cv2  # type: ignore
    except Exception as exc:
        warn(f"frame sampling skipped: cv2 unavailable: {exc}")
        return []
    frames_dir = workdir / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        warn(f"frame sampling failed: could not open {video_path}")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(fps * FRAME_INTERVAL_SECONDS))
    seen_hashes: list[Any] = []
    kept: list[dict[str, Any]] = []
    index = 0
    try:
        while index < total and len(kept) < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                break
            timestamp = index / fps
            try:
                digest = frame_hash(frame)
            except Exception as exc:
                warn(str(exc))
                break
            if is_distinct_hash(digest, seen_hashes):
                seen_hashes.append(digest)
                path = frames_dir / f"frame-{len(kept) + 1:03d}-{int(timestamp):06d}s.jpg"
                cv2.imwrite(str(path), frame)
                kept.append({
                    "timestamp": timestamp,
                    "path": str(path),
                    "hash": str(digest),
                    "ocr_text": ocr_frame(path, ocr_engine) if do_ocr else "",
                })
            index += step
    finally:
        cap.release()
    return kept


def sample_timestamp_keyframes(
    video_path: Path | None,
    workdir: Path,
    timestamps: list[float],
    max_frames: int,
    do_ocr: bool,
    ocr_engine: str | None = None,
) -> list[dict[str, Any]]:
    if video_path is None or not timestamps:
        return []
    try:
        import cv2  # type: ignore
    except Exception as exc:
        warn(f"timestamp frame extraction skipped: cv2 unavailable: {exc}")
        return []
    frames_dir = workdir / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        warn(f"timestamp frame extraction failed: could not open {video_path}")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    kept: list[dict[str, Any]] = []
    seen: set[int] = set()
    try:
        for timestamp in timestamps:
            if len(kept) >= max_frames:
                break
            whole_second = int(max(0, timestamp))
            if whole_second in seen:
                continue
            seen.add(whole_second)
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
            ok, frame = cap.read()
            if not ok:
                warn(f"timestamp frame extraction failed at {format_ts(timestamp)}")
                continue
            path = frames_dir / f"frame-{len(kept) + 1:03d}-{whole_second:06d}s.jpg"
            cv2.imwrite(str(path), frame)
            kept.append({
                "timestamp": float(timestamp),
                "path": str(path),
                "hash": "",
                "ocr_text": ocr_frame(path, ocr_engine) if do_ocr else "",
                "selection": "timestamp",
            })
    finally:
        cap.release()
    return kept


def suggest_frame_timestamps(
    transcript: list[dict[str, Any]],
    max_timestamps: int,
    focus_terms: list[str] | None = None,
) -> list[float]:
    if max_timestamps < 1:
        return []
    terms = [term.lower() for term in [*DEFAULT_KEYFRAME_TERMS, *(focus_terms or [])] if term]
    scored: list[tuple[int, float]] = []
    for item in transcript:
        text = str(item.get("text") or "").lower()
        if not text:
            continue
        score = sum(1 for term in terms if term in text)
        if score <= 0:
            continue
        start = float(item.get("start") or 0.0)
        scored.append((score, max(0.0, start)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected: list[float] = []
    seen_seconds: set[int] = set()
    for _, timestamp in scored:
        for candidate in (max(0.0, timestamp - 2.0), timestamp, timestamp + 2.0):
            whole_second = int(candidate)
            if whole_second in seen_seconds:
                continue
            seen_seconds.add(whole_second)
            selected.append(candidate)
            if len(selected) >= max_timestamps:
                return selected
    return selected


def looks_figure_like(frame: dict[str, Any]) -> bool:
    text = (frame.get("ocr_text") or "").lower()
    if any(word in text for word in ("figure", "chart", "graph", "axis", "axes", "trend", "diagram", "plot", "%")):
        return True
    return len(text.splitlines()) <= 6


def describe_figures(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raise RetiredInterpretationRouteError(
        f"describe_figures is retired; {RETIRED_INTERPRETATION_MESSAGE}"
    )


def summarize_transcript(transcript: list[dict[str, Any]]) -> str:
    raise RetiredInterpretationRouteError(
        f"summarize_transcript is retired; {RETIRED_INTERPRETATION_MESSAGE}"
    )


def format_ts(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def evidence_quality(transcript: list[dict[str, Any]], frames: list[dict[str, Any]], figures: list[dict[str, Any]], summary: str) -> str:
    has_transcript = bool(transcript)
    has_ocr = any((frame.get("ocr_text") or "").strip() for frame in frames)
    has_figures = bool(figures)
    if has_transcript and (has_ocr or has_figures):
        return "transcript-plus-visual"
    if has_transcript:
        return "transcript-only"
    if has_ocr or has_figures:
        return "visual-only"
    if summary:
        return "summary-only"
    return "insufficient"


def build_stage_status(
    media: DownloadedMedia,
    transcript: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    summary: str,
    args: argparse.Namespace,
    partial: bool = False,
) -> dict[str, Any]:
    return {
        "metadata": "ok" if media.metadata else "missing",
        "audio_download": "ok" if media.audio_path else ("not-requested" if transcript else "missing"),
        "video_download": "ok" if media.video_path else ("not-requested" if args.transcript_only else "missing"),
        "transcript": "ok" if transcript else "missing",
        "frame_extract": "not-requested" if args.transcript_only else ("ok" if frames else "missing"),
        "ocr": "not-requested" if args.transcript_only or args.no_ocr else ("ok" if any((frame.get("ocr_text") or "").strip() for frame in frames) else "missing"),
        "vision": "not-requested" if not args.vision else ("ok" if figures else "missing"),
        "summary": "not-requested" if not args.summary else ("ok" if summary else "missing"),
        "artifact": "partial" if partial else "complete",
        "evidence_quality": evidence_quality(transcript, frames, figures, summary),
    }


def render_markdown(data: dict[str, Any]) -> str:
    meta = data["metadata"]
    lines = [
        "# Video Analysis Artifact",
        "",
        "## Metadata",
        "",
        f"- Title: {meta.get('title') or ''}",
        f"- Uploader: {meta.get('uploader') or ''}",
        f"- URL: {meta.get('url') or data.get('url') or ''}",
        f"- Duration: {format_ts(meta.get('duration')) if meta.get('duration') else ''}",
        "",
    ]
    if data.get("stage_status"):
        lines += ["## Stage Status", ""]
        for key, value in data["stage_status"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
    if data.get("summary"):
        lines += ["## Summary", "", data["summary"], ""]
    lines += ["## Transcript", ""]
    if data.get("transcript"):
        for item in data["transcript"]:
            lines.append(f"- [{format_ts(item.get('start'))}] {item.get('text', '')}")
    else:
        lines.append("_No transcript available._")
    lines += ["", "## Slide OCR", ""]
    ocr_items = [frame for frame in data.get("frames", []) if frame.get("ocr_text")]
    if ocr_items:
        for frame in ocr_items:
            lines.append(f"### {format_ts(frame.get('timestamp'))}")
            lines.append("")
            lines.append("```")
            lines.append(frame["ocr_text"])
            lines.append("```")
            lines.append("")
    else:
        lines.append("_No OCR text available._")
        lines.append("")
    lines += ["## Figure Descriptions", ""]
    if data.get("figures"):
        for fig in data["figures"]:
            lines.append(f"- [{format_ts(fig.get('timestamp'))}] {fig.get('description', '')} ({fig.get('path')})")
    else:
        lines.append("_No figure descriptions available._")
    lines += ["", "## Keyframes", ""]
    if data.get("frames"):
        for frame in data["frames"]:
            lines.append(f"- [{format_ts(frame.get('timestamp'))}] {frame.get('path')}")
    else:
        lines.append("_No keyframes kept._")
    lines.append("")
    return "\n".join(lines)


def analyze(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    reject_retired_interpretation_flags(args)
    raise RetiredVideoRouteError(RETIRED_VIDEO_ROUTE_MESSAGE)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        reject_retired_interpretation_flags(args)
    except RetiredInterpretationRouteError as exc:
        parser.error(str(exc))
    parser.error(RETIRED_VIDEO_ROUTE_MESSAGE)


if __name__ == "__main__":
    raise SystemExit(main())
