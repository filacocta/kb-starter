#!/usr/bin/env python3
"""Analyze a video URL into a structured KB ingestion artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FRAME_INTERVAL_SECONDS = 2.0
DEDUP_DISTANCE = 6
VISION_FRAME_CAP = 15
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


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
    parser.add_argument("--vision", action="store_true", help="Use agent CLI to describe likely figures/charts.")
    parser.add_argument("--summary", action="store_true", help="Summarize transcript text with DeepSeek.")
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-frames", type=int, default=40)
    return parser


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


def transcribe_with_whisper(audio_path: Path | None, model_name: str, device: str) -> list[dict[str, Any]]:
    if audio_path is None:
        warn("ASR skipped: no audio download available")
        return []
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        warn(f"faster-whisper unavailable: {exc}")
        return []
    try:
        model = WhisperModel(model_name, device=device)
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
) -> list[dict[str, Any]]:
    captions = fetch_youtube_captions(url, media.metadata)
    if captions:
        return captions
    if media.audio_path is None:
        media.audio_path = download_with_yt_dlp(url, workdir, "audio", "bestaudio/best")
    return transcribe_with_whisper(media.audio_path, model_name, device)


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


def ocr_frame(path: Path) -> str:
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


def sample_keyframes(video_path: Path | None, workdir: Path, max_frames: int, do_ocr: bool) -> list[dict[str, Any]]:
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
                    "ocr_text": ocr_frame(path) if do_ocr else "",
                })
            index += step
    finally:
        cap.release()
    return kept


def looks_figure_like(frame: dict[str, Any]) -> bool:
    text = (frame.get("ocr_text") or "").lower()
    if any(word in text for word in ("figure", "chart", "graph", "axis", "axes", "trend", "diagram", "plot", "%")):
        return True
    return len(text.splitlines()) <= 6


def describe_figures(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if shutil.which("claude") is None:
        warn("vision skipped: claude CLI not found")
        return []
    descriptions: list[dict[str, Any]] = []
    candidates = [frame for frame in frames if looks_figure_like(frame)][:VISION_FRAME_CAP]
    for frame in candidates:
        path = frame["path"]
        prompt = (
            f"Read the image at {path} and describe the figure/chart/diagram "
            "(axes, trend, takeaway) in 2-3 sentences. If it's just text/bullets, say 'no figure'."
        )
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--allowedTools", "Read"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception as exc:
            warn(f"vision failed for {path}: {exc}")
            continue
        if proc.returncode != 0:
            warn(f"vision failed for {path}: {proc.stderr.strip() or proc.returncode}")
            continue
        text = proc.stdout.strip()
        if text and text.lower() != "no figure":
            descriptions.append({"timestamp": frame["timestamp"], "path": path, "description": text})
        time.sleep(1)
    return descriptions


def summarize_transcript(transcript: list[dict[str, Any]]) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        warn("summary skipped: DEEPSEEK_API_KEY is not set")
        return ""
    text = "\n".join(f"[{format_ts(item['start'])}] {item['text']}" for item in transcript)
    if not text.strip():
        warn("summary skipped: no transcript text")
        return ""
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "Summarize this video transcript for a knowledge-base source note. Keep factual claims anchored to the transcript."},
            {"role": "user", "content": text[:120000]},
        ],
    }
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - fixed API endpoint.
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        warn(f"summary failed: {exc}")
        return ""


def format_ts(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


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
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="kb-video-analyze-"))
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out).expanduser().resolve() if args.out else workdir / "analysis.md"
    if args.max_frames < 1:
        raise ValueError("--max-frames must be positive")

    need_video = not args.transcript_only
    media = yt_dlp_extract(args.url, workdir, need_audio=False, need_video=need_video)
    transcript = get_transcript_with_fallback(args.url, workdir, media, args.whisper_model, args.device)
    frames: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    if not args.transcript_only:
        frames = sample_keyframes(media.video_path, workdir, args.max_frames, do_ocr=not args.no_ocr)
        if args.vision:
            figures = describe_figures(frames)
    summary = summarize_transcript(transcript) if args.summary else ""
    data = {
        "url": args.url,
        "workdir": str(workdir),
        "out": str(out_path),
        "metadata": media.metadata,
        "transcript": transcript,
        "summary": summary,
        "frames": frames,
        "figures": figures,
        "keyframe_paths": [frame["path"] for frame in frames],
        "downloads": {
            "audio": str(media.audio_path) if media.audio_path else None,
            "video": str(media.video_path) if media.video_path else None,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(data), encoding="utf-8")

    has_any = bool(media.metadata.get("title") or transcript or frames or figures or summary)
    return data, 0 if has_any else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, code = analyze(args)
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        warn(f"total failure: {exc}")
        result = {"url": args.url, "metadata": {"url": args.url}, "transcript": [], "frames": [], "figures": [], "summary": ""}
        code = 1
    if args.json:
        dump(result)
    else:
        warn(f"wrote {result.get('out')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
