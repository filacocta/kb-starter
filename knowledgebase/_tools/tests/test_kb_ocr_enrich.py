from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-ocr-enrich.py"


def load_ocr_module():
    spec = importlib.util.spec_from_file_location("kb_ocr_enrich", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mistral_retains_exact_page_markdown_before_display_cleanup(tmp_path, monkeypatch):
    import io
    import urllib.request

    module = load_ocr_module()
    monkeypatch.setattr(module, "MISTRAL_OCR_MODEL", "test-model")
    image = tmp_path / "test.png"
    image.write_bytes(b"synthetic-image")
    raw_pages = ["  A\r\n![diagram](https://example.test/figure.png)\n", "\t", "中文\n## OCR\n````\n"]
    response = {"pages": [{"markdown": value} for value in raw_pages]}
    calls = []

    def fake_request(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(module, "_mistral_api_key", lambda: "synthetic-not-a-credential")
    monkeypatch.setattr(urllib.request, "urlopen", fake_request)
    result = module.run_mistral_ocr_result(image)
    assert calls == ["https://api.mistral.ai/v1/ocr"]
    assert [item.raw_text for item in result.attempts] == raw_pages
    assert [item.engine for item in result.attempts] == ["mistral page 1", "mistral page 2", "mistral page 3"]
    assert result.raw_text == "\n\n".join(raw_pages)
    assert result.cleaned_text == "A\r\n[figure]\n\n中文\n## OCR\n````"
    rendered = module.format_ocr_raw_section(result)
    assert "https://example.test/figure.png" in rendered
    assert "synthetic-not-a-credential" not in rendered
    assert "synthetic-image" not in rendered
    for raw in raw_pages:
        assert hashlib.sha256(raw.encode("utf-8")).hexdigest() in rendered
    assert module.run_mistral_ocr(image) == result.cleaned_text


@pytest.mark.parametrize("response", [{}, {"pages": None}, {"pages": [None]}, {"pages": [{}]}, {"pages": [{"foo": "bar"}]}, {"pages": [{"markdown": 5}]}])
def test_mistral_malformed_page_output_fails_closed(tmp_path, monkeypatch, response):
    import io
    import urllib.request

    module = load_ocr_module()
    monkeypatch.setattr(module, "MISTRAL_OCR_MODEL", "test-model")
    image = tmp_path / "test.png"
    image.write_bytes(b"synthetic-image")
    monkeypatch.setattr(module, "_mistral_api_key", lambda: "synthetic-not-a-credential")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(RuntimeError):
        module.run_mistral_ocr_result(image)


def test_mistral_bounds_response_read_without_retaining_a_truncation(tmp_path, monkeypatch):
    import io
    import urllib.request

    module = load_ocr_module()
    monkeypatch.setattr(module, "MISTRAL_OCR_MODEL", "test-model")
    image = tmp_path / "test.png"
    image.write_bytes(b"synthetic-image")
    monkeypatch.setattr(module, "MAX_MISTRAL_RESPONSE_BYTES", 64)
    monkeypatch.setattr(module, "_mistral_api_key", lambda: "synthetic")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b" " * 65))
    with pytest.raises(RuntimeError, match="input limit"):
        module.run_mistral_ocr_result(image)


def test_oversized_raw_result_leaves_existing_note_exactly_unchanged(tmp_path, monkeypatch):
    module = load_ocr_module()
    root, note, _image = make_note(tmp_path)
    original = note.read_bytes()
    monkeypatch.setattr(module, "MAX_OCR_RESULT_BYTES", 64)
    result = module._single_ocr_result("tesseract", "中" * 22, "")
    monkeypatch.setattr(module, "_run_selected_ocr_result", lambda *a: result)
    outcome = module.enrich_note(note, root, do_ocr=True, do_vision=False, dry_run=False)
    assert outcome["written"] is False
    assert "retention limit" in outcome["error"]
    assert note.read_bytes() == original


def test_ocr_attempt_count_is_bounded(monkeypatch):
    module = load_ocr_module()
    monkeypatch.setattr(module, "MAX_OCR_ATTEMPTS", 2)
    attempt = module.OCRAttempt("test", "", "")
    with pytest.raises(module.OCRGuardError, match="attempt/page limit"):
        module._validate_result_size(module.OCRResult("test", "", "", (attempt,) * 3))


def test_mistral_http_error_body_is_never_read_or_echoed(tmp_path, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    class UnreadableBody(io.BytesIO):
        def read(self, *args):
            raise AssertionError("error body must not be read")

    module = load_ocr_module()
    monkeypatch.setattr(module, "MISTRAL_OCR_MODEL", "test-model")
    image = tmp_path / "test.png"
    image.write_bytes(b"synthetic-image")
    monkeypatch.setattr(module, "_mistral_api_key", lambda: "synthetic-secret")
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.test", 429, "synthetic-secret", {}, UnreadableBody(b"synthetic-secret"))
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="^Mistral OCR HTTP 429$"):
        module.run_mistral_ocr_result(image)


@pytest.mark.parametrize("opener,closer", [("```text", ""), ("```text", "```json"), ("~~~", "~~~yaml"), ("````", "```")])
def test_unclosed_fence_fails_preflight_without_engine_or_note_changes(tmp_path, monkeypatch, capsys, opener, closer):
    module = load_ocr_module()
    body = BODY.replace("*Pending.*", "*Pending.*\n\n" + opener + "\nraw\n" + closer, 1)
    root, note, _image = make_note(tmp_path, body=body)
    original = note.read_bytes()
    def unexpected(*args):
        raise AssertionError("engine invoked before fence validation")
    monkeypatch.setattr(module, "_run_selected_ocr_result", unexpected)
    code, outcome = run_main(module, capsys, root, note, "--force")
    assert code == 2
    assert "unclosed Markdown fence" in json.dumps(outcome)
    assert note.read_bytes() == original


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_fence_parser_preserves_following_sections_and_ignores_indented_code(ending):
    module = load_ocr_module()
    body = "## OCR\n\n    ```\n    indented code\n   ````text\n## fake heading\n```json\n   `````  \n\n## Vision Description\n\nKeep vision.\n\n## Notes\n\nKeep context.\n".replace("\n", ending)
    updated = module.replace_section(body, "OCR", "replacement")
    assert updated.endswith("## Vision Description\n\nKeep vision.\n\n## Notes\n\nKeep context.\n".replace("\n", ending))
    assert "## fake heading" not in updated


def test_backtick_in_opener_info_is_not_a_fence():
    module = load_ocr_module()
    assert module.replace_section("## OCR\n```bad`info\n## Notes\nkeep\n", "OCR", "new").endswith("## Notes\nkeep\n")


FRONTMATTER = """---
author: kb-bot
type: media-description
media_status: captured
media_kind: image
local_path: _media/images/example.png
---
"""

BODY = """# Example image

## OCR

*Pending.*

## Vision Description

*Pending.*

## Notes

Keep this context.
"""


def make_note(
    tmp_path: Path,
    *,
    frontmatter: str = FRONTMATTER,
    body: str = BODY,
    local_path: str | None = None,
) -> tuple[Path, Path, Path]:
    kb_root = tmp_path / "knowledgebase"
    note = kb_root / "topics" / "example" / "notes" / "media" / "image.md"
    note.parent.mkdir(parents=True)
    if local_path is not None:
        frontmatter = frontmatter.replace("_media/images/example.png", local_path)
    note.write_text(frontmatter + body, encoding="utf-8")
    image = kb_root / "_media" / "images" / "example.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"fixture image")
    return kb_root, note, image


def run_main(module, capsys, root: Path, note: Path, *extra: str) -> tuple[int, dict]:
    code = module.main(
        [
            "--kb-root",
            str(root),
            "--note",
            str(note),
            "--ocr-only",
            "--ocr-engine",
            "tesseract",
            *extra,
        ]
    )
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("argv", [[], ["--vision-only"], ["--both"]])
def test_vision_modes_fail_before_file_or_provider_activity(
    tmp_path: Path, monkeypatch, capsys, argv: list[str]
) -> None:
    module = load_ocr_module()
    kb_root = tmp_path / "not-created"
    calls: list[str] = []

    def unexpected(*args, **kwargs):
        calls.append("unexpected activity")
        raise AssertionError("retired vision mode performed activity")

    monkeypatch.setattr(module, "find_media_notes", unexpected)
    monkeypatch.setattr(module, "enrich_note", unexpected)
    monkeypatch.setattr(module, "run_gemini_vision", unexpected)

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--kb-root", str(kb_root), *argv])

    assert exc_info.value.code == 2
    assert calls == []
    assert not kb_root.exists()
    error = capsys.readouterr().err.lower()
    assert "vision interpretation is retired" in error
    assert "inspect the retained image" in error
    assert "active native kb worker" in error
    assert "no cli simulation" in error


def test_run_gemini_vision_fails_before_reading_image() -> None:
    module = load_ocr_module()
    missing_image = Path("/tmp/retired-vision-image-does-not-exist.png")

    with pytest.raises(module.RetiredVisionRouteError) as exc_info:
        module.run_gemini_vision(missing_image)

    message = str(exc_info.value).lower()
    assert "inspect the retained image" in message
    assert "active native kb worker" in message
    assert "no cli simulation" in message


def test_enrich_note_vision_fails_before_reading_note(tmp_path: Path) -> None:
    module = load_ocr_module()
    missing_note = tmp_path / "missing.md"

    with pytest.raises(module.RetiredVisionRouteError) as exc_info:
        module.enrich_note(
            missing_note,
            tmp_path / "knowledgebase",
            do_ocr=True,
            do_vision=True,
            dry_run=True,
        )

    message = str(exc_info.value).lower()
    assert "inspect the retained image" in message
    assert "active native kb worker" in message
    assert not missing_note.exists()


@pytest.mark.parametrize("force", [False, True])
def test_ocr_only_dry_run_skips_ocr_and_write(
    tmp_path: Path, monkeypatch, capsys, force: bool
) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    before = note.read_bytes()
    calls: list[Path] = []

    def unexpected_ocr(image_path: Path) -> str:
        calls.append(image_path)
        raise AssertionError("dry-run invoked OCR")

    monkeypatch.setattr(module, "run_tesseract", unexpected_ocr)
    argv = [
        "--kb-root",
        str(kb_root),
        "--note",
        str(note),
        "--ocr-only",
        "--ocr-engine",
        "tesseract",
        "--dry-run",
    ]
    if force:
        argv.append("--force")

    assert module.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    result = summary["results"][0]
    assert result["ocr"] == "dry-run: would run tesseract OCR"
    assert result["written"] is False
    assert summary["written"] == 0
    assert calls == []
    assert note.read_bytes() == before


def test_apply_ocr_uses_released_lock_and_preserves_mode(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    note.chmod(0o640)
    calls: list[str] = []

    def fake_ocr(image_path: Path) -> str:
        # A second nonblocking acquisition proves the provider call occurs
        # after the short snapshot lock has been released.
        with module._write_guard.write_lock(kb_root, nonblocking=True):
            calls.append(str(image_path))
        return "mocked OCR"

    monkeypatch.setattr(module, "run_tesseract", fake_ocr)
    code, result = run_main(module, capsys, kb_root, note)

    assert code == 0
    assert result["written"] == 1
    assert result["results"][0]["written"] is True
    assert calls == [str(kb_root / "_media/images/example.png")]
    assert stat.S_IMODE(note.stat().st_mode) == 0o640
    assert "mocked OCR" in note.read_text(encoding="utf-8")


def test_filled_section_skips_without_force_and_force_replaces(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    filled = BODY.replace("*Pending.*", "Existing OCR", 1)
    kb_root, note, _image = make_note(tmp_path, body=filled)
    calls: list[Path] = []

    def fake_ocr(image_path: Path) -> str:
        calls.append(image_path)
        return "forced OCR"

    monkeypatch.setattr(module, "run_tesseract", fake_ocr)
    code, skipped = run_main(module, capsys, kb_root, note)
    assert code == 0
    assert skipped["results"][0]["skipped"] is True
    assert skipped["results"][0]["written"] is False
    assert calls == []

    code, forced = run_main(module, capsys, kb_root, note, "--force")
    assert code == 0
    assert forced["results"][0]["written"] is True
    assert calls == [kb_root / "_media/images/example.png"]
    assert "forced OCR" in note.read_text(encoding="utf-8")


def test_engine_failure_is_nonzero_and_never_writes(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    before = note.read_bytes()

    def failed_ocr(_image_path: Path) -> str:
        raise RuntimeError("mock engine failed")

    monkeypatch.setattr(module, "run_tesseract", failed_ocr)
    code, result = run_main(module, capsys, kb_root, note)

    assert code == 2
    item = result["results"][0]
    assert item["written"] is False
    assert "OCR error: mock engine failed" in item["error"]
    assert note.read_bytes() == before


def test_commit_failure_is_nonzero_and_explicitly_not_written(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    before = note.read_bytes()
    monkeypatch.setattr(module, "run_tesseract", lambda _path: "mocked OCR")

    def failed_commit(*_args, **_kwargs):
        raise module._write_guard.WriteGuardError("mock commit failed")

    monkeypatch.setattr(module._write_guard, "atomic_write", failed_commit)
    code, result = run_main(module, capsys, kb_root, note)

    assert code == 2
    item = result["results"][0]
    assert item["written"] is False
    assert "commit error" in item["error"]
    assert note.read_bytes() == before


def test_concurrent_note_change_is_rejected_even_with_force(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    filled = BODY.replace("*Pending.*", "Old OCR", 1)
    kb_root, note, _image = make_note(tmp_path, body=filled)

    def changing_ocr(_image_path: Path) -> str:
        note.write_text(note.read_text(encoding="utf-8") + "\nconcurrent edit\n", encoding="utf-8")
        return "new OCR"

    monkeypatch.setattr(module, "run_tesseract", changing_ocr)
    code, result = run_main(module, capsys, kb_root, note, "--force")

    assert code == 2
    item = result["results"][0]
    assert item["written"] is False
    assert "stale" in item["error"]
    assert "concurrent edit" in note.read_text(encoding="utf-8")


def test_concurrent_image_change_is_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_ocr_module()
    kb_root, note, image = make_note(tmp_path)

    def changing_ocr(_image_path: Path) -> str:
        image.write_bytes(b"changed image")
        return "new OCR"

    monkeypatch.setattr(module, "run_tesseract", changing_ocr)
    code, result = run_main(module, capsys, kb_root, note)

    assert code == 2
    assert result["results"][0]["written"] is False
    assert "stale" in result["results"][0]["error"]


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.md",
        "topics/../topics/example/notes/media/image.md",
        "topics/example/notes/media/other.txt",
        "topics/example/sources/source.md",
    ],
)
def test_invalid_note_paths_fail_before_engine(
    tmp_path: Path, monkeypatch, capsys, bad_path: str
) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(module, "run_tesseract", lambda path: calls.append(path) or "bad")

    bad_note = Path(bad_path)
    code = module.main(
        [
            "--kb-root",
            str(kb_root),
            "--note",
            str(bad_note),
            "--ocr-only",
            "--ocr-engine",
            "tesseract",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert calls == []
    assert output["written"] == 0
    assert not note.read_text(encoding="utf-8").endswith("bad")


@pytest.mark.parametrize(
    "frontmatter_change, expected_code",
    [
        (lambda value: value.replace("author: kb-bot", "author: human"), 0),
        (lambda value: value.replace("media_kind: image", "media_kind: video"), 0),
        (lambda value: value.replace("media_status: captured", "media_status: discovered"), 0),
        (lambda value: value.replace("author: kb-bot\n", "author: kb-bot\nauthor: human\n"), 2),
        (lambda value: value.replace("media_kind: image\n", "media_kind: image\nmalformed: [\n"), 2),
    ],
)
def test_invalid_yaml_or_eligibility_fails_before_engine(
    tmp_path: Path, monkeypatch, capsys, frontmatter_change, expected_code
) -> None:
    module = load_ocr_module()
    original = FRONTMATTER
    changed = frontmatter_change(original)
    kb_root, note, _image = make_note(tmp_path, frontmatter=changed)
    calls: list[Path] = []
    monkeypatch.setattr(module, "run_tesseract", lambda path: calls.append(path) or "bad")

    code, result = run_main(module, capsys, kb_root, note)
    assert code == expected_code
    assert calls == []
    assert result["results"][0]["written"] is False


@pytest.mark.parametrize(
    "local_path, setup",
    [
        ("/tmp/escape.png", "none"),
        ("../_media/images/example.png", "none"),
        ("topics/example/notes/media/image.md", "none"),
        ("_media/images/link.png", "symlink"),
        ("_media/images/hard.png", "hardlink"),
        ("_media/images/fifo.png", "fifo"),
    ],
)
def test_invalid_image_paths_fail_before_engine(
    tmp_path: Path, monkeypatch, capsys, local_path: str, setup: str
) -> None:
    module = load_ocr_module()
    kb_root, note, image = make_note(tmp_path, local_path=local_path)
    target = kb_root / "_media/images" / Path(local_path).name
    if setup == "symlink":
        target.symlink_to(image)
    elif setup == "hardlink":
        os.link(image, target)
    elif setup == "fifo":
        os.mkfifo(target)
    calls: list[Path] = []
    monkeypatch.setattr(module, "run_tesseract", lambda path: calls.append(path) or "bad")

    code, result = run_main(module, capsys, kb_root, note)
    assert code == 2
    assert calls == []
    assert result["results"][0]["written"] is False


def test_idempotence_does_not_replace_complete_note(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    monkeypatch.setattr(module, "run_tesseract", lambda _path: "stable OCR")

    first_code, first = run_main(module, capsys, kb_root, note)
    first_bytes = note.read_bytes()
    first_identity = (note.stat().st_dev, note.stat().st_ino)
    second_code, second = run_main(module, capsys, kb_root, note)

    assert first_code == 0 and second_code == 0
    assert first["results"][0]["written"] is True
    assert second["results"][0]["skipped"] is True
    assert second["results"][0]["written"] is False
    assert note.read_bytes() == first_bytes
    assert (note.stat().st_dev, note.stat().st_ino) == first_identity


def test_unlimited_result_retains_raw_utf8_and_newlines_before_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    module = load_ocr_module()
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture image")
    binary = tmp_path / "llama-mtmd-cli"
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    for path in (binary, model, mmproj):
        path.write_bytes(b"fixture")
    monkeypatch.setattr(module, "UNLIMITED_OCR_BIN", str(binary))
    monkeypatch.setattr(module, "UNLIMITED_OCR_MODEL", str(model))
    monkeypatch.setattr(module, "UNLIMITED_OCR_MMPROJ", str(mmproj))
    raw_bytes = (
        "Visible\r\n输出所有字符.\r\n<|det|>chart [0,0,1,1]<|/det|>\r\n"
        "No text detected\r\n"
    ).encode("utf-8")
    calls: list[dict] = []

    def fake_run(_cmd, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout=raw_bytes, stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.run_unlimited_ocr_result(image)

    assert result.raw_text == raw_bytes.decode("utf-8")
    assert "\r\n" in result.raw_text
    assert "输出所有字符." in result.raw_text
    assert "输出所有字符." in result.cleaned_text
    assert "No text detected" not in result.cleaned_text
    assert "[chart]" in result.cleaned_text
    assert calls[0]["text"] is False
    assert calls[0]["capture_output"] is True


def test_hf_cleanup_is_derived_and_never_replaces_raw_text() -> None:
    module = load_ocr_module()
    raw = (
        "Actual row\n"
        "输出所有字符.\n"
        "according to rule this is ground truth\n"
        "the OCR output is hallucinated\n"
        "Actual row\n"
        "[image]\n"
    )
    cleaned = module.clean_unlimited_hf_output(raw)
    result = module._single_ocr_result("unlimited-hf", raw, cleaned)

    assert result.raw_text == raw
    assert "输出所有字符." in result.raw_text
    assert "ground truth" in result.raw_text
    assert "hallucinated" in result.raw_text
    assert result.cleaned_text == "Actual row\n输出所有字符.\nActual row"


def test_local_cleanup_preserves_multilingual_source_text() -> None:
    module = load_ocr_module()
    lines = [
        "English label",
        "中文标签",
        "日本語のラベル",
        "한국어 라벨",
        "Étiquette française",
        "Etiqueta española",
        "العربية",
    ]
    raw = "\n".join(lines)

    unlimited_cleaned = module.clean_unlimited_output(raw)
    hf_cleaned = module.clean_unlimited_hf_output(raw)

    assert unlimited_cleaned == raw
    assert hf_cleaned == raw
    assert module._single_ocr_result("unlimited", raw, unlimited_cleaned).raw_text == raw


def test_hf_result_retains_decoded_stdout_before_eval_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    module = load_ocr_module()
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture image")
    venv = tmp_path / "python"
    helper = tmp_path / "kb-ocr-hf-infer.py"
    venv.write_bytes(b"fixture")
    helper.write_bytes(b"fixture")
    monkeypatch.setattr(module, "_KB_OCR_HF_VENV", str(venv))
    monkeypatch.setattr(module, "_KB_OCR_HF_HELPER", str(helper))
    raw_bytes = (
        "Actual row\r\n"
        "输出所有字符.\r\n"
        "according to rule this is ground truth\r\n"
        "the OCR output is hallucinated\r\n"
        "Actual row\r\n"
    ).encode("utf-8")
    calls: list[dict] = []

    def fake_run(_cmd, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout=raw_bytes, stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.run_unlimited_hf_ocr_result(image)

    assert result.raw_text == raw_bytes.decode("utf-8")
    assert "\r\n" in result.raw_text
    assert "ground truth" in result.raw_text
    assert "hallucinated" in result.raw_text
    assert result.cleaned_text == "Actual row\n输出所有字符.\nActual row"
    assert calls[0]["text"] is False
    assert calls[0]["capture_output"] is True


def test_fenced_combined_heading_is_not_treated_as_a_note_section() -> None:
    module = load_ocr_module()
    body = (
        "## OCR\n\n"
        "```text\n"
        "## OCR / Vision Description\n"
        "model output\n"
        "```\n\n"
        "## Vision Description\n\n"
        "*Pending.*\n"
    )

    assert module.normalize_combined_section(body) == body


def test_rendering_fences_headings_and_backticks_without_section_corruption() -> None:
    module = load_ocr_module()
    derived = "Visible\n## Vision Description\n```nested```\n"
    raw = "Raw\n## OCR Raw\n````nested fence````\n"
    rendered = module.format_ocr_section(derived)
    opening = rendered.splitlines()[0]
    assert opening.startswith("````")
    assert "## Vision Description" in rendered
    assert "````nested fence````" not in rendered

    result = module._single_ocr_result("unlimited", raw, derived)
    body = module.replace_section(BODY, "OCR", rendered)
    body = module.insert_section(
        body,
        "OCR Raw",
        module.format_ocr_raw_section(result),
        before_heading="Vision Description",
    )
    assert module.section_content(body, "Vision Description").strip() == "*Pending.*"
    assert "## Vision Description" in (module.section_content(body, "OCR") or "")
    assert raw in (module.section_content(body, "OCR Raw") or "")
    assert "## OCR Raw" in (module.section_content(body, "OCR Raw") or "")


def test_enrichment_stores_raw_and_cleaned_channels_with_integrity_metadata(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    kb_root, note, _image = make_note(tmp_path)
    raw = "Visible\r\n输出所有字符.\r\naccording to rule: ground truth\r\nNo text detected\r\n"
    cleaned = module.clean_unlimited_hf_output(raw)
    ocr_result = module._single_ocr_result("unlimited-hf", raw, cleaned)
    monkeypatch.setattr(module, "_run_selected_ocr_result", lambda *_args: ocr_result)

    code = module.main(
        [
            "--kb-root",
            str(kb_root),
            "--note",
            str(note),
            "--ocr-only",
            "--ocr-engine",
            "unlimited-hf",
        ]
    )
    capsys.readouterr()
    assert code == 0
    updated = note.read_bytes().decode("utf-8")
    assert raw.encode("utf-8") in note.read_bytes()
    metadata, body = module.parse_frontmatter(updated)
    assert metadata["author"] == "kb-bot"
    raw_section = module.section_content(body, "OCR Raw") or ""
    cleaned_section = module.section_content(body, "OCR") or ""
    assert "Attempt 1: `unlimited-hf`" in raw_section
    assert '**Source:** "_media/images/example.png"' in raw_section
    assert raw in raw_section
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() in raw_section
    assert "输出所有字符." in raw_section
    assert "ground truth" in raw_section
    assert "输出所有字符." in cleaned_section
    assert "ground truth" not in cleaned_section
    assert "Visible" in cleaned_section
    assert "## Vision Description" in body


def test_auto_engine_alias_is_rejected_before_kb_io(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    kb_root = tmp_path / "not-created"
    calls: list[str] = []

    def unexpected(*args, **kwargs):
        calls.append("unexpected activity")
        raise AssertionError("retired auto route performed activity")

    monkeypatch.setattr(module, "find_media_notes", unexpected)
    monkeypatch.setattr(module, "enrich_note", unexpected)
    monkeypatch.setattr(module, "run_unlimited_ocr_result", unexpected)
    monkeypatch.setattr(module, "run_unlimited_hf_ocr_result", unexpected)

    with pytest.raises(SystemExit) as exc_info:
        module.main(
            [
                "--kb-root",
                str(kb_root),
                "--note",
                str(kb_root / "note.md"),
                "--ocr-only",
                "--ocr-engine",
                "auto",
            ]
        )

    assert exc_info.value.code == 2
    assert calls == []
    assert not kb_root.exists()
    error = capsys.readouterr().err.lower()
    assert "ocr engine 'auto' is retired" in error
    assert "explicit" in error


def test_auto_dispatcher_alias_is_rejected_before_engine_call(monkeypatch) -> None:
    module = load_ocr_module()
    monkeypatch.setattr(
        module,
        "run_auto_ocr_result",
        lambda *_args: pytest.fail("retired auto engine was invoked"),
    )

    with pytest.raises(module.OCRGuardError, match="auto.*retired"):
        module._run_selected_ocr_result(Path("unused.png"), "auto")


def test_legacy_filled_note_without_raw_remains_valid_and_skips(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_ocr_module()
    filled = BODY.replace("*Pending.*", "Existing OCR", 1)
    kb_root, note, _image = make_note(tmp_path, body=filled)
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_run_selected_ocr_result",
        lambda *_args: calls.append("unexpected") or module._single_ocr_result("test", "raw", "cleaned"),
    )

    code = module.main(
        [
            "--kb-root",
            str(kb_root),
            "--note",
            str(note),
            "--ocr-only",
            "--ocr-engine",
            "tesseract",
        ]
    )
    capsys.readouterr()
    assert code == 0
    assert calls == []
    assert "## OCR Raw" not in note.read_text(encoding="utf-8")
