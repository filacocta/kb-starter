from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
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
    assert args.ocr_engine is None
    assert args.max_frames == 12


@pytest.mark.parametrize("flag", ["--vision", "--summary"])
def test_retired_cli_flags_fail_before_reexec_or_io(monkeypatch, tmp_path, capsys, flag) -> None:
    video = load_video_module()
    calls = []

    monkeypatch.setattr(video, "maybe_reexec_tools_venv", lambda: calls.append("reexec"))
    monkeypatch.setattr(video, "yt_dlp_extract", lambda *args, **kwargs: calls.append("download"))

    workdir = tmp_path / "work"
    out = tmp_path / "analysis.md"
    with pytest.raises(SystemExit) as exc_info:
        video.main(
            [
                "https://example.com/video",
                flag,
                "--workdir",
                str(workdir),
                "--out",
                str(out),
            ]
        )

    assert exc_info.value.code == 2
    assert calls == []
    assert not workdir.exists()
    assert not out.exists()
    error = capsys.readouterr().err
    assert "extract transcript/frames" in error
    assert "active native KB worker" in error
    assert "no CLI simulation" in error


@pytest.mark.parametrize("flag", ["--vision", "--summary"])
def test_analyze_rejects_retired_flags_before_workdir_or_download(monkeypatch, tmp_path, flag) -> None:
    video = load_video_module()
    calls = []
    monkeypatch.setattr(video, "yt_dlp_extract", lambda *args, **kwargs: calls.append("download"))

    workdir = tmp_path / "work"
    out = tmp_path / "analysis.md"
    args = video.build_parser().parse_args(
        ["https://example.com/video", flag, "--workdir", str(workdir), "--out", str(out)]
    )

    with pytest.raises(video.RetiredInterpretationRouteError) as exc_info:
        video.analyze(args)

    assert "extract transcript/frames" in str(exc_info.value)
    assert "active native KB worker" in str(exc_info.value)
    assert calls == []
    assert not workdir.exists()
    assert not out.exists()


def test_direct_interpretation_helpers_fail_closed() -> None:
    video = load_video_module()
    for call in (
        lambda: video.describe_figures([{"path": "/tmp/frame.jpg"}]),
        lambda: video.summarize_transcript([{"start": 0.0, "text": "transcript"}]),
    ):
        with pytest.raises(video.RetiredInterpretationRouteError) as exc_info:
            call()
        message = str(exc_info.value)
        assert "extract transcript/frames" in message
        assert "active native KB worker" in message
        assert "no CLI simulation" in message


def test_evidence_quality_values_are_frozen() -> None:
    video = load_video_module()
    expected = {
        "transcript-plus-visual",
        "transcript-only",
        "visual-only",
        "summary-only",
        "insufficient",
    }

    assert video.EVIDENCE_QUALITY_VALUES == expected
    cases = [
        ([{"text": "hello"}], [{"ocr_text": "slide"}], [], ""),
        ([{"text": "hello"}], [], [], ""),
        ([], [{"ocr_text": "slide"}], [], ""),
        ([], [], [{"path": "figure.png"}], ""),
        ([], [], [], "short summary"),
        ([], [], [], ""),
    ]

    for transcript, frames, figures, summary in cases:
        assert video.evidence_quality(transcript, frames, figures, summary) in video.EVIDENCE_QUALITY_VALUES


def test_suggest_frame_timestamps_uses_visual_and_focus_terms() -> None:
    video = load_video_module()
    transcript = [
        {"start": 5.0, "end": 6.0, "text": "intro and housekeeping"},
        {"start": 40.0, "end": 42.0, "text": "now look at this dashboard chart"},
        {"start": 90.0, "end": 92.0, "text": "the shannon signal appears here"},
    ]

    timestamps = video.suggest_frame_timestamps(transcript, 5, ["shannon"])

    assert timestamps == [38.0, 40.0, 42.0, 88.0, 90.0]


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


def test_mocked_captions_helper_remains_testable(monkeypatch) -> None:
    video = load_video_module()

    class FakeApi:
        def fetch(self, video_id):
            assert video_id == "abc123DEF45"
            return [{"start": 1.5, "duration": 2.0, "text": "Hello\nworld"}]

    fake_module = types.SimpleNamespace(YouTubeTranscriptApi=FakeApi)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

    result = video.fetch_youtube_captions("https://www.youtube.com/watch?v=abc123DEF45", {})
    assert result == [
        {"start": 1.5, "end": 3.5, "text": "Hello world", "source": "youtube-captions"}
    ]


@pytest.mark.parametrize("flags", [[], ["--transcript-only"], ["--no-ocr"], ["--json"]])
def test_url_entrypoint_retired_before_any_io(monkeypatch, tmp_path, capsys, flags) -> None:
    video = load_video_module()
    for name in ("maybe_reexec_tools_venv", "yt_dlp_extract", "get_transcript_with_fallback"):
        monkeypatch.setattr(video, name, lambda *a, **kw: pytest.fail("retirement must precede I/O"))
    out = tmp_path / "analysis.md"
    work = tmp_path / "work"
    argv = ["https://example.com/video", "--out", str(out), "--workdir", str(work), *flags]
    with pytest.raises(SystemExit) as exc:
        video.main(argv)
    assert exc.value.code == 2
    assert "kb-media-inspect.py" in capsys.readouterr().err
    assert not out.exists() and not work.exists()

    with pytest.raises(video.RetiredVideoRouteError, match="unbounded URL/video analyzer is retired"):
        video.analyze(video.build_parser().parse_args(argv))
    assert not out.exists() and not work.exists()
