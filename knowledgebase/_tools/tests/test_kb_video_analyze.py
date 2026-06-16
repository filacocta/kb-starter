from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-video-analyze.py"


def load_video_module():
    spec = importlib.util.spec_from_file_location("kb_video_analyze", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_argparse_wiring() -> None:
    video = load_video_module()
    args = video.build_parser().parse_args(
        [
            "https://example.com/video",
            "--out",
            "/tmp/out.md",
            "--json",
            "--workdir",
            "/tmp/work",
            "--transcript-only",
            "--no-ocr",
            "--vision",
            "--summary",
            "--whisper-model",
            "small",
            "--device",
            "cpu",
            "--max-frames",
            "12",
        ]
    )

    assert args.url == "https://example.com/video"
    assert args.out == "/tmp/out.md"
    assert args.json is True
    assert args.workdir == "/tmp/work"
    assert args.transcript_only is True
    assert args.no_ocr is True
    assert args.vision is True
    assert args.summary is True
    assert args.whisper_model == "small"
    assert args.device == "cpu"
    assert args.max_frames == 12


def test_imagehash_dedup_synthetic_frames() -> None:
    video = load_video_module()
    white = Image.new("RGB", (64, 64), "white")
    near_white = Image.new("RGB", (64, 64), (250, 250, 250))
    striped = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(striped)
    for offset in range(-64, 64, 8):
        draw.line((offset, 0, offset + 64, 64), fill="black", width=2)

    first = video.frame_hash(white)
    duplicate = video.frame_hash(near_white)
    distinct = video.frame_hash(striped)

    seen = [first]
    assert video.is_distinct_hash(duplicate, seen) is False
    assert video.is_distinct_hash(distinct, seen) is True


def test_mocked_captions_path(monkeypatch, tmp_path, capsys) -> None:
    video = load_video_module()
    extract_calls = []

    class FakeApi:
        def fetch(self, video_id):
            assert video_id == "abc123DEF45"
            return [{"start": 1.5, "duration": 2.0, "text": "Hello\nworld"}]

    fake_module = types.SimpleNamespace(YouTubeTranscriptApi=FakeApi)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

    def fake_extract(url, workdir, need_audio, need_video):
        extract_calls.append((need_audio, need_video))
        return video.DownloadedMedia(
            metadata={
                "title": "Demo",
                "uploader": "Tester",
                "url": url,
                "duration": 10,
                "id": "abc123DEF45",
                "extractor": "Youtube",
            },
            audio_path=None,
            video_path=None,
        )

    monkeypatch.setattr(video, "yt_dlp_extract", fake_extract)
    monkeypatch.setattr(video, "download_with_yt_dlp", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audio download should not run")))
    monkeypatch.setattr(video, "transcribe_with_whisper", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ASR should not run")))

    out = tmp_path / "analysis.md"
    code = video.main(["https://www.youtube.com/watch?v=abc123DEF45", "--transcript-only", "--out", str(out), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["metadata"]["title"] == "Demo"
    assert payload["transcript"] == [
        {"start": 1.5, "end": 3.5, "text": "Hello world", "source": "youtube-captions"}
    ]
    assert extract_calls == [(False, False)]
    assert payload["frames"] == []
    assert out.exists()
    assert "Hello world" in out.read_text(encoding="utf-8")
