from __future__ import annotations

import importlib.util
import io
import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "kb-media-capture.py"
    spec = importlib.util.spec_from_file_location("temporary_capture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("url", [
    "http://example.com/v.mp4", "file:///tmp/v.mp4", "https://user:secret@example.com/v.mp4",
    "https://example.com:8443/v.mp4", "https://example.com/v.m3u8", "https://example.com/v.mp4#extra",
    "https://example.com/v.mp4\n", "https://example.com/page", "https://example.com/v.svg",
])
def test_invalid_url_rejected(url):
    with pytest.raises(ValueError):
        module().validate_inspection_url(url)


def test_redirect_rejected():
    with pytest.raises(ValueError, match="redirects"):
        module().RejectInspectionRedirect().redirect_request(None, None, 302, "", {}, "https://other/v.mp4")


class Response(io.BytesIO):
    status = 200
    headers = {"content-type": "video/mp4"}

    def geturl(self):
        return "https://example.com/v.mp4"


def test_bounded_single_request_and_closed_response(monkeypatch):
    mod = module()
    response = Response(b"12345")
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return response

    monkeypatch.setattr(mod.urllib.request, "build_opener", lambda *handlers: Opener())
    with pytest.raises(ValueError, match="bytes-cap"):
        mod.fetch_inspection_bytes("https://example.com/v.mp4", 4)
    assert calls == [("https://example.com/v.mp4", 30)]
    assert response.closed


@pytest.mark.parametrize("extra", [[], ["--stage-dir", "_tools/staging/media/run", "--max-bytes", "26214401"]])
def test_stage_and_size_required_before_fetch(tmp_path, monkeypatch, extra):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: pytest.fail("must not fetch"))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", *extra,
    ])
    result, code = mod.capture(args)
    assert code == 1
    assert "requires --stage-dir" in result["failed"][0]["reason"]


def test_temporary_stages_only(tmp_path, monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: (b"video", "video/mp4", None))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", "--stage-dir", "_tools/staging/media/run",
    ])
    result, code = mod.capture(args)
    assert code == 0
    assert not result["captured"][0]["committed"]
    assert Path(result["captured"][0]["staged_path"]).read_bytes() == b"video"
    assert not (tmp_path / "_media").exists()


def test_temporary_no_write_refused_before_fetch_or_staging(tmp_path, monkeypatch, capsys):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: pytest.fail("must not fetch"))
    monkeypatch.setattr(mod, "fetch_bytes", lambda *a: pytest.fail("must not fetch"))
    argv = [
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", "--no-write",
        "--stage-dir", "_tools/staging/media/run",
    ]
    result, code = mod.capture(mod.build_parser().parse_args(argv))
    assert code == 1
    assert result["captured"] == []
    assert "--no-write is not supported" in result["failed"][0]["reason"]
    assert mod.main(argv) == 1
    assert "--no-write is not supported" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_duplicate_urls_refused_before_any_request(tmp_path, monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: pytest.fail("must not fetch"))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "https://example.com/v.mp4",
        "--source-slug", "sample", "--kb-root", str(tmp_path),
        "--temporary-inspection", "--stage-dir", "_tools/staging/media/run",
    ])
    result, code = mod.capture(args)
    assert code == 1
    assert "duplicate URLs" in result["failed"][0]["reason"]
    assert not (tmp_path / "_tools").exists()


def test_expected_hash_mismatch_does_not_stage(tmp_path, monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: (b"changed", "video/mp4", None))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", "--stage-dir", "_tools/staging/media/run",
        "--expected-media-id", "0" * 64,
    ])
    result, code = mod.capture(args)
    assert code == 1
    assert result["failed"][0]["reason"] == "expected-media-id-mismatch"
    assert not (tmp_path / "_tools").exists()


def test_temporary_waits_for_publication_lock_without_refetch(tmp_path, monkeypatch):
    mod = module()
    calls = []
    def fetch(*args):
        calls.append("fetch")
        return b"video", "video/mp4", None
    @contextmanager
    def lock(root, *, nonblocking):
        calls.append(("lock", nonblocking))
        yield
    monkeypatch.setattr(mod, "fetch_inspection_bytes", fetch)
    monkeypatch.setattr(mod.media_guard(), "write_lock", lock)
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", "--stage-dir", "_tools/staging/media/run",
        "--expected-media-id", hashlib.sha256(b"video").hexdigest(),
    ])
    result, code = mod.capture(args)
    assert code == 0
    assert calls == ["fetch", ("lock", False)]


@pytest.mark.parametrize("expected", ["", "not-a-hash", "A" * 64, "0" * 63])
def test_bad_expected_hash_refused_before_fetch(tmp_path, monkeypatch, expected):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: pytest.fail("must not fetch"))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "--source-slug", "sample",
        "--kb-root", str(tmp_path), "--temporary-inspection", "--stage-dir", "_tools/staging/media/run",
        "--expected-media-id", expected,
    ])
    result, code = mod.capture(args)
    assert code == 1
    assert "full lowercase SHA-256" in result["failed"][0]["reason"]


def test_expected_hash_with_two_urls_refused_before_fetch(tmp_path, monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "fetch_inspection_bytes", lambda *a: pytest.fail("must not fetch"))
    args = mod.build_parser().parse_args([
        "--capture", "https://example.com/v.mp4", "https://example.com/other.mp4",
        "--source-slug", "sample", "--kb-root", str(tmp_path), "--temporary-inspection",
        "--stage-dir", "_tools/staging/media/run", "--expected-media-id", "0" * 64,
    ])
    result, code = mod.capture(args)
    assert code == 1
    assert "one temporary-inspection URL" in result["failed"][0]["reason"]
