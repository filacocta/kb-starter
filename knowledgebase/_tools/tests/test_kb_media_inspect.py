from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-media-inspect.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_media_inspect_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "knowledgebase"
    staging = root / "_tools" / "staging" / "media"
    staging.mkdir(parents=True)
    return root, staging


def write_image(path: Path) -> None:
    Image.new("RGB", (24, 16), (40, 120, 200)).save(path, format="PNG")


def args_for(module, root: Path, input_path: Path, output_dir: Path, **overrides):
    supplied_hash = overrides.get("input_sha256")
    data = dict(
        kb_root=root,
        input_path=input_path,
        input_sha256=(supplied_hash if supplied_hash is not None else hashlib.sha256(input_path.read_bytes()).hexdigest()),
        origin_url="https://example.test/post/asset",
        output_dir=output_dir,
        max_frames=40,
        interval_seconds=5.0,
        timeout_seconds=600.0,
        run_id=None,
        asset_id=None,
        asr=False,
        ocr=False,
        whisper_model="Systran/faster-whisper-base",
        delete_staged_input=None,
        receipt=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def fake_probe(module, *, duration=11.0, has_audio=False):
    return module.VideoProbe(
        duration_seconds=duration,
        width=320,
        height=424,
        has_audio=has_audio,
        video_codec="h264",
        audio_codec="aac" if has_audio else None,
        raw={},
    )


def test_run_ocr_reports_all_failed_frames(monkeypatch, tmp_path):
    module = load_module()
    frames = [(tmp_path / "frame-001.jpg", 0.0), (tmp_path / "frame-002.jpg", 1.0)]
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        module,
        "_run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, b"", b"bad frame"
        ),
    )

    state, items = module.run_ocr(frames, deadline=module.time.monotonic() + 10)

    assert state == "failed"
    assert [item["frame"] for item in items] == ["frame-001.jpg", "frame-002.jpg"]
    assert all(item["status"] == "failed" for item in items)
    assert all("bad frame" in item["error"] for item in items)
    assert all("text" not in item for item in items)


def test_run_ocr_preserves_successful_results_with_mixed_failures(monkeypatch, tmp_path):
    module = load_module()
    frames = [
        (tmp_path / "frame-001.jpg", 0.0),
        (tmp_path / "frame-002.jpg", 1.0),
        (tmp_path / "frame-003.jpg", 2.0),
    ]
    responses = iter([
        subprocess.CompletedProcess([], 0, b"visible words\n", b""),
        subprocess.CompletedProcess([], 2, b"", b"engine failed"),
        subprocess.CompletedProcess([], 0, b"", b""),
    ])
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(module, "_run_process", lambda *args, **kwargs: next(responses))

    state, items = module.run_ocr(frames, deadline=module.time.monotonic() + 10)

    assert state == "partial"
    assert items[0] == {
        "timestamp_seconds": 0.0,
        "frame": "frame-001.jpg",
        "status": "ok",
        "text": "visible words",
    }
    assert items[1]["status"] == "failed"
    assert items[1]["frame"] == "frame-002.jpg"
    assert "engine failed" in items[1]["error"]
    assert items[2]["status"] == "ok"
    assert items[2]["text"] == ""


def test_run_ocr_distinguishes_successful_empty_text(monkeypatch, tmp_path):
    module = load_module()
    frames = [(tmp_path / "frame-001.jpg", 0.0), (tmp_path / "frame-002.jpg", 1.0)]
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        module,
        "_run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b" \n", b""),
    )

    state, items = module.run_ocr(frames, deadline=module.time.monotonic() + 10)

    assert state == "ok-no-text"
    assert len(items) == 2
    assert all(item["status"] == "ok" and item["text"] == "" for item in items)


def test_image_inspection_records_original_without_copying(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    output = staging / "inspection-image"

    result, code = module.inspect_media(args_for(module, root, source, output))

    assert code == 0
    assert result["state"] == "INSPECTION_COMPLETE"
    receipt_path = root / result["receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["author"] == "kb-bot"
    assert receipt["input"]["kind"] == "image"
    assert receipt["input"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["input"]["duration_seconds"] is None
    assert receipt["created_files"] == []
    assert source.exists()
    assert list(output.iterdir()) == [receipt_path]


@pytest.mark.parametrize("flags", [("--ocr",), ("--asr",), ("--ocr", "--asr")])
def test_image_extraction_flags_refused_before_output(tmp_path, monkeypatch, capsys, flags):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    original = source.read_bytes()
    output = staging / "unsupported-image-extraction"
    monkeypatch.setattr(module, "_mkdir_fresh", lambda *a: pytest.fail("must not create output"))
    monkeypatch.setattr(module, "_run_process", lambda *a, **k: pytest.fail("must not run an engine"))
    options = {"ocr": "--ocr" in flags, "asr": "--asr" in flags}
    with pytest.raises(module.MediaInspectError, match="video-only"):
        module.inspect_media(args_for(module, root, source, output, **options))
    assert module.main([
        "--kb-root", str(root), "--input", str(source),
        "--input-sha256", hashlib.sha256(original).hexdigest(),
        "--origin-url", "https://example.test/post/asset",
        "--output-dir", str(output), *flags,
    ]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "STOP"
    assert not output.exists()
    assert source.read_bytes() == original


def test_video_sampling_is_regular_and_capped(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-video"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=11.0))
    timestamps = []

    def fake_extract(input_path, output_path, timestamp, *, timeout):
        timestamps.append(timestamp)
        output_path.write_bytes(f"frame-{timestamp:.3f}".encode())

    monkeypatch.setattr(module, "extract_frame", fake_extract)
    result, code = module.inspect_media(args_for(module, root, source, output, max_frames=40, interval_seconds=5.0))

    assert code == 0
    assert timestamps == [0.0, 11.0/3, 22.0/3]
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["sampling"]["frames_created"] == 3
    assert len(receipt["created_files"]) == 3
    assert all(item["path"].endswith(".jpg") for item in receipt["created_files"])
    assert all(item["size"] > 0 for item in receipt["created_files"])


@pytest.mark.parametrize(
    ("responses", "ocr_state", "inspection_state", "code"),
    [
        (
            [
                subprocess.CompletedProcess([], 1, b"", b"first failed"),
                subprocess.CompletedProcess([], 2, b"", b"second failed"),
            ],
            "failed",
            "INSPECTION_PARTIAL",
            2,
        ),
        (
            [
                subprocess.CompletedProcess([], 0, b"recognized\n", b""),
                subprocess.CompletedProcess([], 1, b"", b"second failed"),
            ],
            "partial",
            "INSPECTION_PARTIAL",
            2,
        ),
        (
            [
                subprocess.CompletedProcess([], 0, b" \n", b""),
                subprocess.CompletedProcess([], 0, b"\n", b""),
            ],
            "ok-no-text",
            "INSPECTION_COMPLETE",
            0,
        ),
    ],
)
def test_video_ocr_records_failed_frames_and_empty_success(
    monkeypatch, tmp_path, responses, ocr_state, inspection_state, code
):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / f"inspection-ocr-{ocr_state}"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=2.0))
    monkeypatch.setattr(
        module,
        "extract_frame",
        lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"),
    )
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/tesseract")
    response_iter = iter(responses)
    monkeypatch.setattr(module, "_run_process", lambda *args, **kwargs: next(response_iter))

    result, actual_code = module.inspect_media(
        args_for(module, root, source, output, ocr=True, max_frames=2, interval_seconds=1.0)
    )

    assert actual_code == code
    assert result["state"] == inspection_state
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["ocr"]["state"] == ocr_state
    assert receipt["ocr"]["frames_succeeded"] == (0 if ocr_state == "failed" else 1 if ocr_state == "partial" else 2)
    assert receipt["ocr"]["frames_failed"] == (2 if ocr_state == "failed" else 1 if ocr_state == "partial" else 0)
    ocr_path = root / receipt["ocr"]["path"]
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    assert len(ocr["frames"]) == 2
    if ocr_state == "failed":
        assert all(item["status"] == "failed" for item in ocr["frames"])
    elif ocr_state == "partial":
        assert ocr["frames"][0]["text"] == "recognized"
        assert ocr["frames"][1]["status"] == "failed"
    else:
        assert all(item["status"] == "ok" and item["text"] == "" for item in ocr["frames"])
    assert '"recognized"' not in json.dumps(receipt)
    assert all("text" not in failure for failure in receipt["ocr"]["failures"])


def test_ocr_timeout_preserves_failures_and_skips_remaining_frames(monkeypatch, tmp_path):
    module = load_module()
    frames = [(tmp_path / f"frame-{number}.jpg", float(number)) for number in range(3)]
    calls = []
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/tesseract")

    def timeout(*args, **kwargs):
        calls.append(args)
        raise module.MediaInspectTimeout("shared deadline expired")

    monkeypatch.setattr(module, "_run_process", timeout)
    state, items = module.run_ocr(frames, deadline=module.time.monotonic() + 10)

    assert state == "failed"
    assert len(calls) == 1
    assert [item["status"] for item in items] == ["failed", "skipped", "skipped"]
    assert [item["frame"] for item in items] == [path.name for path, _ in frames]
    assert all("text" not in item and len(item["error"]) <= 500 for item in items)


def test_frame_limit_and_sampling_arguments_fail_before_output(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    for value in (0, 41):
        output = staging / f"inspection-{value}"
        with pytest.raises(module.MediaInspectError, match="max frames"):
            module.inspect_media(args_for(module, root, source, output, max_frames=value))
        assert not output.exists()
    with pytest.raises(module.MediaInspectError, match="sampling interval"):
        module.inspect_media(args_for(module, root, source, staging / "inspection-interval", interval_seconds=0))


def test_duration_bounds_are_checked_from_ffprobe_json(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")

    def result_for(duration):
        payload = {
            "streams": [{"codec_type": "video", "width": 320, "height": 240, "duration": duration}],
            "format": {"duration": duration},
        }
        return subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(module, "_run_process", lambda *args, **kwargs: result_for("181.0"))
    with pytest.raises(module.MediaInspectError, match="exceeds 180"):
        module.probe_video(source)

    monkeypatch.setattr(module, "_run_process", lambda *args, **kwargs: result_for("nan"))
    with pytest.raises(module.MediaInspectError, match="finite"):
        module.probe_video(source)


def test_ffprobe_command_is_file_pipe_only(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    calls = []

    payload = {
        "streams": [{"codec_type": "video", "width": 320, "height": 240, "duration": "1.0"}],
        "format": {"duration": "1.0"},
    }

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.probe_video(source, timeout=5)
    command = calls[0][0]
    assert "-protocol_whitelist" in command
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_asr_is_not_called_when_ffprobe_reports_no_audio(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-no-audio"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0, has_audio=False))
    monkeypatch.setattr(module, "extract_frame", lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"))
    monkeypatch.setattr(module, "transcribe_offline", lambda *args, **kwargs: pytest.fail("ASR must not run without audio"))

    result, code = module.inspect_media(args_for(module, root, source, output, asr=True))

    assert code == 0
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["asr"]["state"] == "skipped-no-audio"
    assert not (output / "transcript.json").exists()
    assert any("no audio" in item.lower() for item in receipt["evidence_limitations"])


def test_decoder_timeout_writes_failed_or_partial_receipt(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-timeout"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0))

    def timeout_frame(*args, **kwargs):
        raise module.MediaInspectTimeout("ffmpeg frame timed out")

    monkeypatch.setattr(module, "extract_frame", timeout_frame)
    result, code = module.inspect_media(args_for(module, root, source, output, max_frames=1, timeout_seconds=1.0))

    assert code == 2
    assert result["state"] == "INSPECTION_FAILED"
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["created_files"] == []
    assert "timed out" in receipt["evidence_limitations"][-1]


def test_asr_worker_uses_local_cpu_int8_configuration(monkeypatch):
    module = load_module()
    calls = {}

    class Segment:
        start = 1.25
        end = 2.75
        text = "local words"

    class Info:
        language = "en"

    class FakeModel:
        def __init__(self, model_name, **kwargs):
            calls["model_name"] = model_name
            calls.update(kwargs)

        def transcribe(self, path):
            calls["path"] = path
            return [Segment()], Info()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    results = []

    class Queue:
        def put(self, value):
            results.append(value)

    module._asr_worker("/safe/staged.mp4", "Systran/faster-whisper-base", Queue())

    assert calls["model_name"] == "Systran/faster-whisper-base"
    assert calls["device"] == "cpu"
    assert calls["compute_type"] == "int8"
    assert calls["local_files_only"] is True
    assert results == [{"segments": [{"start_seconds": 1.25, "end_seconds": 2.75, "text": "local words"}], "language": "en"}]


def test_asr_quality_rejects_segment_outside_clip():
    module = load_module()

    usable, quality = module.validate_asr_segments(
        [{"start_seconds": 0.0, "end_seconds": 2.01, "text": "words"}],
        duration_seconds=1.0,
    )

    assert usable == []
    assert quality["status"] == "unusable"
    assert quality["excluded_segment_count"] == 1
    assert any("outside clip" in reason for reason in quality["reasons"])


def test_asr_quality_rejects_empty_and_punctuation_only_speech():
    module = load_module()

    usable, quality = module.validate_asr_segments(
        [
            {"start_seconds": 0.0, "end_seconds": 0.5, "text": "..."},
            {"start_seconds": 0.5, "end_seconds": 1.0, "text": "   "},
        ],
        duration_seconds=1.0,
    )

    assert usable == []
    assert quality["status"] == "unusable"
    assert quality["excluded_segment_count"] == 2
    assert all("empty or punctuation-only" in reason for reason in quality["reasons"])


def test_asr_quality_accepts_valid_segments_without_interpretation():
    module = load_module()
    segments = [
        {"start_seconds": 0.0, "end_seconds": 0.5, "text": "first words"},
        {"start_seconds": 0.5, "end_seconds": 1.0, "text": "second words"},
    ]

    usable, quality = module.validate_asr_segments(segments, duration_seconds=1.0)

    assert quality["status"] == "valid"
    assert usable == segments
    assert quality["reasons"] == []


def test_asr_quality_warns_on_repeated_content_but_does_not_reject_it():
    module = load_module()
    segments = [
        {
            "start_seconds": float(index * 2),
            "end_seconds": float(index * 2 + 0.5),
            "text": "the same repeated phrase",
        }
        for index in range(5)
    ]

    usable, quality = module.validate_asr_segments(segments, duration_seconds=10.0)

    assert quality["status"] == "valid"
    assert len(usable) == 5
    assert quality["reasons"] == []
    assert quality["warnings"][0]["kind"] == "repeated-content"
    assert quality["warnings"][0]["segment_indices"] == [0, 1, 2, 3, 4]
    assert "ground truth" in quality["warnings"][0]["message"]


def test_asr_quality_excludes_observed_degenerate_tiny_repetition():
    module = load_module()
    segments = [
        {"start_seconds": float(index * 30), "end_seconds": float(index * 30 + 1), "text": "You"}
        for index in range(5)
    ]

    usable, quality = module.validate_asr_segments(segments, duration_seconds=127.566)

    assert usable == []
    assert quality["status"] == "unusable"
    assert quality["excluded_segment_count"] == 5
    assert all("degenerate-repetition" in reason for reason in quality["reasons"])


def test_asr_quality_failure_keeps_raw_transcript_private_and_marks_partial(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-asr-quality"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0, has_audio=True))
    monkeypatch.setattr(
        module,
        "extract_frame",
        lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"),
    )
    raw_segments = [{"start_seconds": 0.0, "end_seconds": 5.0, "text": "private speech"}]
    monkeypatch.setattr(module, "transcribe_offline", lambda *args, **kwargs: ("ok", raw_segments, "en"))

    result, code = module.inspect_media(args_for(module, root, source, output, asr=True, max_frames=1))

    assert code == 2
    assert result["state"] == "INSPECTION_PARTIAL"
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["asr"]["state"] == "unusable"
    assert receipt["asr"]["quality"]["status"] == "unusable"
    assert receipt["asr"]["quality"]["reasons"]
    transcript_path = root / receipt["asr"]["transcript_path"]
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert transcript["raw_output_status"] == "unvalidated-excluded"
    assert transcript["raw_segments"] == raw_segments
    assert transcript["validated_segments"] == []
    assert "private speech" not in json.dumps(receipt)

    cleanup_args = SimpleNamespace(
        kb_root=root,
        receipt=root / result["receipt"],
        delete_staged_input=None,
        reviewed_disposition=None,
        review_ref=None,
    )
    with pytest.raises(module.MediaInspectError, match="incomplete"):
        module.cleanup_media(cleanup_args)

    review_ref = root / "independent-review.md"
    review_ref.write_text("reviewed unusable ASR; discard scratch after custody checks\n", encoding="utf-8")
    cleanup_args.reviewed_disposition = "unusable-asr"
    sibling_scratch = staging / "another-inspection"
    sibling_scratch.mkdir()
    sibling_receipt = sibling_scratch / "receipt.json"
    sibling_receipt.write_text("{}\n", encoding="utf-8")
    cleanup_args.review_ref = sibling_receipt
    with pytest.raises(module.MediaInspectError, match="media inspection scratch"):
        module.cleanup_media(cleanup_args)
    cleanup_args.review_ref = review_ref
    cleaned, cleaned_code = module.cleanup_media(cleanup_args)
    assert cleaned_code == 0
    assert cleaned["reviewed_disposition"] == "unusable-asr"
    assert cleaned["review_ref"] == "independent-review.md"
    assert not transcript_path.exists()
    repeated, repeated_code = module.cleanup_media(
        SimpleNamespace(
            kb_root=root,
            receipt=root / result["receipt"],
            delete_staged_input=None,
        )
    )
    assert repeated_code == 0
    assert repeated["reviewed_disposition"] == "unusable-asr"
    assert repeated["already_absent"]
    completed_receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert completed_receipt["state"] == "CLEANUP_COMPLETE"
    assert completed_receipt["cleanup"]["review_ref"] == "independent-review.md"


def test_asr_artifact_write_error_still_publishes_partial_receipt(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-asr-write-error"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0, has_audio=True))
    monkeypatch.setattr(
        module,
        "extract_frame",
        lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"),
    )
    monkeypatch.setattr(
        module,
        "transcribe_offline",
        lambda *args, **kwargs: ("ok", [{"start_seconds": 0.0, "end_seconds": 0.5, "text": "words"}], "en"),
    )
    monkeypatch.setattr(module, "_write_json_artifact", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    result, code = module.inspect_media(args_for(module, root, source, output, asr=True, max_frames=1))

    assert code == 2
    assert result["state"] == "INSPECTION_PARTIAL"
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["state"] == "INSPECTION_PARTIAL"
    assert receipt["asr"]["state"] == "failed"
    assert "disk full" in receipt["error"]
    assert (output / "frame-001-000000.000s.jpg").exists()


def test_changed_input_hash_is_rejected_without_output(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    output = staging / "inspection-changed"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(b"changed")
    with pytest.raises(module.MediaInspectError, match="SHA-256 mismatch"):
        module.inspect_media(args_for(module, root, source, output, input_sha256=expected))
    assert not output.exists()


def test_verify_snapshot_accepts_unchanged_input(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    snapshot = module.read_snapshot(source, expected)
    module.verify_snapshot_unchanged(snapshot)


def test_verify_snapshot_rehash_rejects_same_size_bytes_with_restored_mtime(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot = module.read_snapshot(source, expected)
    original_stat = source.stat()

    source.write_bytes(b"B" * snapshot.size)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(module.MediaInspectError, match="bytes changed after validation"):
        module.verify_snapshot_unchanged(snapshot)


def test_inspection_rejects_same_size_input_change_with_restored_mtime(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    original = b"offline-video-placeholder"
    changed = b"offline-video-placeholdeR"
    assert len(original) == len(changed)
    source.write_bytes(original)
    output = staging / "inspection-input-rehashed"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0))
    changed_once = False

    def extract_and_change(input_path, output_path, timestamp, *, timeout):
        nonlocal changed_once
        output_path.write_bytes(b"frame")
        if not changed_once:
            changed_once = True
            original_stat = input_path.stat()
            input_path.write_bytes(changed)
            os.utime(input_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    monkeypatch.setattr(module, "extract_frame", extract_and_change)

    result, code = module.inspect_media(args_for(module, root, source, output, max_frames=1))

    assert code == 2
    assert result["state"] == "INSPECTION_PARTIAL"
    receipt = json.loads((root / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["state"] == "INSPECTION_PARTIAL"
    assert "bytes changed after validation" in receipt["error"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_input_links_and_special_files_are_rejected(tmp_path, kind):
    module = load_module()
    root, staging = make_kb(tmp_path)
    original = staging / "original.png"
    write_image(original)
    target = staging / f"{kind}.png"
    if kind == "symlink":
        target.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, target)
    else:
        target.unlink(missing_ok=True)
        os.mkfifo(target)
    output = staging / f"inspection-{kind}"
    expected = hashlib.sha256(original.read_bytes()).hexdigest()
    with pytest.raises(module.MediaInspectError):
        module.inspect_media(args_for(module, root, target, output, input_sha256=expected))
    assert not output.exists()


def test_outside_and_existing_output_paths_are_rejected(tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(module.MediaInspectError, match="escapes the KB root"):
        module.inspect_media(args_for(module, root, source, outside / "output"))
    existing = staging / "existing"
    existing.mkdir()
    with pytest.raises(module.MediaInspectError, match="already exists"):
        module.inspect_media(args_for(module, root, source, existing))


def test_receipt_manifest_cleanup_refuses_tampering_and_extra_files(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-cleanup"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0))
    monkeypatch.setattr(module, "extract_frame", lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"))
    inspected, code = module.inspect_media(args_for(module, root, source, output, max_frames=1))
    assert code == 0
    receipt_path = root / inspected["receipt"]
    manifest_path = root / inspected["created_files"][0]["path"]

    manifest_path.write_bytes(b"changed")
    with pytest.raises(module.MediaInspectError, match="(hash|size) changed"):
        module.cleanup_media(SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=None))
    assert manifest_path.exists()

    # Restore the exact bytes and then introduce an unlisted file. Cleanup
    # must inspect the complete tree before deleting any listed artifact.
    manifest_path.write_bytes(b"frame")
    extra = output / "unlisted.bin"
    extra.write_bytes(b"do not touch")
    with pytest.raises(module.MediaInspectError, match="extra or unlisted"):
        module.cleanup_media(SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=None))
    assert manifest_path.exists() and extra.exists()


def test_cleanup_is_exact_and_second_run_is_idempotent(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-idempotent"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0))
    monkeypatch.setattr(module, "extract_frame", lambda input_path, output_path, timestamp, *, timeout: output_path.write_bytes(b"frame"))
    inspected, code = module.inspect_media(args_for(module, root, source, output, max_frames=1))
    assert code == 0
    receipt_path = root / inspected["receipt"]
    cleanup_args = SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=None)
    first, first_code = module.cleanup_media(cleanup_args)
    second, second_code = module.cleanup_media(cleanup_args)
    assert first_code == second_code == 0
    assert first["removed_files"]
    assert second["already_absent"] == first["removed_files"]
    assert receipt_path.exists()
    assert source.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "CLEANUP_COMPLETE"
    assert receipt["cleanup"]["state"] == "complete"


def test_cleanup_with_named_input_is_idempotent_after_input_deletion(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    output = staging / "inspection-input-idempotent"
    inspected, code = module.inspect_media(args_for(module, root, source, output))
    assert code == 0
    receipt_path = root / inspected["receipt"]
    cleanup_args = SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=source)

    first, first_code = module.cleanup_media(cleanup_args)
    second, second_code = module.cleanup_media(cleanup_args)

    assert first_code == second_code == 0
    assert first["staged_input_deleted"] is True
    assert second["staged_input_deleted"] is False
    assert second["staged_input_already_absent"] is True
    assert not source.exists()
    assert receipt_path.exists()


def test_cleanup_requires_matching_unchanged_named_input(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "image.png"
    write_image(source)
    output = staging / "inspection-input"
    inspected, code = module.inspect_media(args_for(module, root, source, output))
    assert code == 0
    receipt_path = root / inspected["receipt"]
    source.write_bytes(b"changed")
    cleanup_args = SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=source)
    with pytest.raises(module.MediaInspectError, match="SHA-256 mismatch"):
        module.cleanup_media(cleanup_args)
    assert source.exists() and receipt_path.exists()


def test_cleanup_refuses_incomplete_receipt(monkeypatch, tmp_path):
    module = load_module()
    root, staging = make_kb(tmp_path)
    source = staging / "clip.mp4"
    source.write_bytes(b"offline-video-placeholder")
    output = staging / "inspection-incomplete"
    monkeypatch.setattr(module, "probe_video", lambda path, timeout: fake_probe(module, duration=1.0))
    monkeypatch.setattr(module, "extract_frame", lambda *args, **kwargs: (_ for _ in ()).throw(module.MediaInspectTimeout("timeout")))
    inspected, code = module.inspect_media(args_for(module, root, source, output, max_frames=1))
    assert code == 2
    receipt_path = root / inspected["receipt"]
    with pytest.raises(module.MediaInspectError, match="incomplete"):
        module.cleanup_media(SimpleNamespace(kb_root=root, receipt=receipt_path, delete_staged_input=None))
    review_ref = root / "independent-review.md"
    review_ref.write_text("decoder failure reviewed\n", encoding="utf-8")
    with pytest.raises(module.MediaInspectError, match="incomplete"):
        module.cleanup_media(
            SimpleNamespace(
                kb_root=root,
                receipt=receipt_path,
                delete_staged_input=None,
                reviewed_disposition="unusable-asr",
                review_ref=review_ref,
            )
        )
    assert receipt_path.exists()


def test_bad_kb_root_symlink_is_rejected(tmp_path):
    module = load_module()
    real = tmp_path / "real"
    (real / "_tools" / "staging" / "media").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(module.MediaInspectError, match="symlinks"):
        module.canonical_kb_root(alias)


def test_regular_timestamps_never_reach_video_end_and_never_exceed_bound():
    module = load_module()
    timestamps = module.regular_timestamps(180.0, 40, 0.1)
    assert len(timestamps) == 40
    assert timestamps[0] == 0.0
    assert timestamps[-1] < 180.0
    assert all(later > earlier for earlier, later in zip(timestamps, timestamps[1:]))
