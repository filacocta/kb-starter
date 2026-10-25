from __future__ import annotations

import importlib.util
import hashlib
import fcntl
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-media-capture.py"


def load_media_module():
    spec = importlib.util.spec_from_file_location("kb_media_capture", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_capture_stage_dir_writes_staged_file_not_final_media(tmp_path) -> None:
    media = load_media_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    source = tmp_path / "image.png"
    stage_dir = kb_root / "_tools/staging/media/demo"
    # Minimal PNG header with 1x1 dimensions; enough for the local dimension parser.
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
    )

    args = media.build_parser().parse_args(
        [
            "--capture",
            source.as_uri(),
            "--source-slug",
            "demo-source",
            "--kb-root",
            str(kb_root),
            "--stage-dir",
            str(stage_dir),
        ]
    )
    result, code = media.capture(args)

    assert code == 0
    item = result["captured"][0]
    assert item["committed"] is False
    assert item["staged_path"].startswith(str(stage_dir))
    assert Path(item["staged_path"]).exists()
    assert item["local_path"].startswith("knowledgebase/_media/images/")
    assert item["target_path"] == item["local_path"]
    assert not (kb_root / item["local_path"].removeprefix("knowledgebase/")).exists()


def test_commit_staged_copies_file_to_target_and_preserves_retry_input(tmp_path) -> None:
    media = load_media_module()
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    source = tmp_path / "image.png"
    stage_dir = kb_root / "_tools/staging/media/demo"
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
    )

    capture_args = media.build_parser().parse_args(
        [
            "--capture",
            source.as_uri(),
            "--source-slug",
            "demo-source",
            "--kb-root",
            str(kb_root),
            "--stage-dir",
            str(stage_dir),
        ]
    )
    capture_result, capture_code = media.capture(capture_args)
    item = capture_result["captured"][0]

    assert capture_code == 0
    commit_args = media.build_parser().parse_args(
        [
            "--commit-staged",
            item["staged_path"],
            "--target-path",
            item["target_path"],
            "--media-id",
            item["media_id"],
            "--kb-root",
            str(kb_root),
        ]
    )
    commit_result, commit_code = media.commit_staged(commit_args)

    target = kb_root / item["target_path"].removeprefix("knowledgebase/")
    assert commit_code == 0
    assert commit_result["committed"][0]["committed"] is True
    assert commit_result["committed"][0]["already_present"] is False
    assert target.exists()
    assert Path(item["staged_path"]).exists()

    retry_stage = stage_dir / "retry.png"
    retry_stage.write_bytes(target.read_bytes())
    retry_args = media.build_parser().parse_args(
        [
            "--commit-staged",
            str(retry_stage),
            "--target-path",
            item["target_path"],
            "--media-id",
            item["media_id"],
            "--kb-root",
            str(kb_root),
        ]
    )
    retry_result, retry_code = media.commit_staged(retry_args)

    assert retry_code == 0
    assert retry_result["committed"][0]["already_present"] is True
    assert retry_stage.exists()


def staged_case(tmp_path):
    media = load_media_module()
    root = tmp_path / "knowledgebase"
    root.mkdir()
    stage = tmp_path / "stage.png"
    stage.write_bytes(b"synthetic offline media")
    args = media.build_parser().parse_args([
        "--commit-staged", str(stage), "--kb-root", str(root),
        "--target-path", "knowledgebase/_media/images/2026/09/example.png",
        "--media-id", hashlib.sha256(stage.read_bytes()).hexdigest(),
    ])
    return media, root, stage, root / "_media/images/2026/09/example.png", args


@pytest.mark.parametrize("target", [
    "knowledgebase/topics/test/source.md", "_media/../outside.png",
    "knowledgebase/_media/images/../../outside.png", "/tmp/outside.png",
    "_media/_README.md", "_media/images/script.py", "_media//images/a.png",
    "_media/images/./a.png", "_media/images/a.png\n",
])
def test_staged_bad_destination_fails_without_writes(tmp_path, target):
    media, root, stage, _, args = staged_case(tmp_path)
    args.target_path = target
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"]
    assert stage.exists()
    assert not (root / ".kb-write.lock").exists()
    assert not (root / "_media").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "empty", "oversize"])
def test_unsafe_staging_is_never_consumed(tmp_path, kind):
    media, root, stage, target, args = staged_case(tmp_path)
    other = tmp_path / "other.png"
    if kind == "hardlink":
        os.link(stage, other)
    elif kind == "symlink":
        stage.rename(other)
        stage.symlink_to(other)
    elif kind == "fifo":
        stage.unlink()
        os.mkfifo(stage)
    elif kind == "empty":
        stage.write_bytes(b"")
    else:
        args.max_bytes = 1
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"]
    assert stage.exists()
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


@pytest.mark.parametrize("digest", [None, "abc", "a" * 64, "A" * 64])
def test_expected_digest_is_required_and_checked(tmp_path, digest):
    media, root, stage, target, args = staged_case(tmp_path)
    args.media_id = digest
    _, code = media.commit_staged(args)
    assert code != 0
    assert stage.exists() and not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_preview_has_no_filesystem_writes(tmp_path):
    media, root, stage, target, args = staged_case(tmp_path)
    args.no_write = True
    result, code = media.commit_staged(args)
    assert code == 0 and not result["committed"]
    assert result["planned"][0]["committed"] is False
    assert stage.exists() and not target.exists()
    assert not (root / ".kb-write.lock").exists()
    assert not (root / "_media").exists()


@pytest.mark.parametrize("kind", ["parent-link", "target-link", "target-hardlink", "root-link"])
def test_linked_roots_or_destinations_are_rejected(tmp_path, kind):
    media, root, stage, target, args = staged_case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "parent-link":
        (root / "_media").symlink_to(outside, target_is_directory=True)
    elif kind == "root-link":
        alias = tmp_path / "root-alias"
        alias.symlink_to(root, target_is_directory=True)
        args.kb_root = str(alias)
    else:
        target.parent.mkdir(parents=True)
        other = outside / "media.png"
        other.write_bytes(stage.read_bytes())
        if kind == "target-link":
            target.symlink_to(other)
        else:
            os.link(other, target)
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"]
    assert stage.exists()
    assert not list(root.rglob(".kb-media-*"))


def test_stage_change_between_preflight_and_lock_stops(tmp_path, monkeypatch):
    media, root, stage, target, args = staged_case(tmp_path)
    guard = media.media_guard()
    original = guard.write_lock

    @contextmanager
    def changed(*a, **kw):
        stage.write_bytes(b"changed by another writer")
        with original(*a, **kw) as descriptor:
            yield descriptor

    monkeypatch.setattr(guard, "write_lock", changed)
    result, code = media.commit_staged(args)
    assert code != 0 and "changed" in result["failed"][0]["reason"]
    assert stage.read_bytes() == b"changed by another writer"
    assert not target.exists()


def test_racing_target_is_not_overwritten(tmp_path, monkeypatch):
    media, root, stage, target, args = staged_case(tmp_path)
    original = media.publish_media_bytes

    def race(path, data):
        path.write_bytes(b"other writer")
        original(path, data)

    monkeypatch.setattr(media, "publish_media_bytes", race)
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"][0]["publication_may_have_succeeded"]
    assert target.read_bytes() == b"other writer"
    assert stage.exists() and not list(root.rglob(".kb-media-*"))


def test_post_publication_failure_reports_uncertainty_and_retry(tmp_path, monkeypatch):
    media, root, stage, target, args = staged_case(tmp_path)
    original = os.fsync

    def fail_directory(descriptor):
        if target.exists():
            raise OSError("simulated directory sync failure")
        original(descriptor)

    monkeypatch.setattr(media.os, "fsync", fail_directory)
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"][0]["publication_may_have_succeeded"]
    assert target.read_bytes() == stage.read_bytes()
    monkeypatch.setattr(media.os, "fsync", original)
    result, code = media.commit_staged(args)
    assert code == 0 and result["committed"][0]["already_present"]
    assert stage.exists()


@pytest.mark.parametrize("kind", ["busy", "symlink", "hardlink", "fifo"])
def test_commit_rejects_unsafe_or_busy_shared_lock(tmp_path, kind):
    media, root, stage, target, args = staged_case(tmp_path)
    lock = root / ".kb-write.lock"
    other = tmp_path / "other-lock"
    other.write_bytes(b"unchanged")
    held = None
    if kind == "busy":
        held = lock.open("w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif kind == "symlink":
        lock.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, lock)
    else:
        os.mkfifo(lock)
    try:
        result, code = media.commit_staged(args)
        assert code != 0 and result["failed"]
        assert stage.exists() and not target.exists()
        assert other.read_bytes() == b"unchanged"
    finally:
        if held is not None:
            held.close()


@pytest.mark.parametrize("kind", ["root", "media-parent"])
def test_symlink_cycles_return_controlled_failure(tmp_path, kind):
    media, root, stage, target, args = staged_case(tmp_path)
    cycle = tmp_path / "cycle" if kind == "root" else root / "_media"
    cycle.symlink_to(cycle)
    if kind == "root":
        args.kb_root = str(cycle)
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"]
    assert stage.exists()
    assert not (root / ".kb-write.lock").exists()


def test_older_python_symlink_runtime_error_is_controlled(tmp_path, monkeypatch):
    media, root, stage, target, args = staged_case(tmp_path)

    def cycle(*args, **kwargs):
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr(media.media_guard(), "canonical_root", cycle)
    result, code = media.commit_staged(args)
    assert code != 0 and result["failed"][0]["reason"] == "Symlink loop"
    resolved, error = media.resolve_media_target(root, args.target_path)
    assert resolved is None and error == "Symlink loop"
    assert stage.exists() and not target.exists()


def capture_case(tmp_path, monkeypatch, *, staged=False):
    media = load_media_module()
    root = tmp_path / "kb"
    root.mkdir()
    args = media.build_parser().parse_args([
        "--capture", "https://example.test/image.png", "--source-slug", "example",
        "--kb-root", str(root),
    ])
    if staged:
        args.stage_dir = "_tools/staging/media/example"
    data = b"synthetic image bytes"
    monkeypatch.setattr(media, "fetch_bytes", lambda *a: (data, "image/png", None))
    return media, root, args, data


@pytest.mark.parametrize("staged", [False, True])
def test_capture_no_clobber_identical_retry_and_no_network_lock(tmp_path, monkeypatch, staged):
    media, root, args, data = capture_case(tmp_path, monkeypatch, staged=staged)

    def fetch(*unused):
        with media.media_guard().write_lock(root, nonblocking=True):
            pass  # Can acquire it: capture must not hold it during network work.
        return data, "image/png", None

    monkeypatch.setattr(media, "fetch_bytes", fetch)
    first, code = media.capture(args)
    assert code == 0
    item = first["captured"][0]
    path = Path(item["staged_path"]) if staged else root / item["local_path"].removeprefix("knowledgebase/")
    assert path.read_bytes() == data
    assert item["committed"] is (not staged)
    retry, code = media.capture(args)
    assert code == 0 and retry["captured"][0]["already_present"]
    path.write_bytes(b"other writer")
    failed, code = media.capture(args)
    assert code != 0 and failed["failed"]
    assert path.read_bytes() == b"other writer"


@pytest.mark.parametrize("staged", [False, True])
def test_capture_preview_never_writes(tmp_path, monkeypatch, staged):
    media, root, args, _ = capture_case(tmp_path, monkeypatch, staged=staged)
    args.no_write = True
    result, code = media.capture(args)
    assert code == 0 and not result["captured"][0]["committed"]
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("bad", ["external-stage", "root-link", "stage-link", "media-link", "license"])
def test_bad_capture_scope_fails_before_fetch(tmp_path, monkeypatch, bad):
    media, root, args, _ = capture_case(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    if bad == "external-stage":
        args.stage_dir = str(outside)
    elif bad == "root-link":
        link = tmp_path / "link"
        link.symlink_to(root)
        args.kb_root = str(link)
    elif bad == "stage-link":
        (root / "_tools").symlink_to(outside)
        args.stage_dir = "_tools/staging/media/example"
    elif bad == "media-link":
        (root / "_media").symlink_to(outside)
    else:
        args.license = "do-not-redistribute"
    monkeypatch.setattr(media, "fetch_bytes", lambda *a: pytest.fail("must reject before fetch"))
    result, code = media.capture(args)
    assert code != 0 and result["failed"]
    assert not (root / ".kb-write.lock").exists()
    assert list(outside.iterdir()) == []


def media_description(root, data, *, name="example", local="_media/images/2026/09/retained.png"):
    note = root / "topics/example/notes/media" / f"{name}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\nauthor: kb-bot\ntype: media-description\nmedia_status: captured\n"
        f"media_id: {hashlib.sha256(data).hexdigest()}\nlocal_path: knowledgebase/{local}\n---\n",
        encoding="utf-8",
    )
    path = root / local
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return note, path


def test_capture_reuses_only_verified_dedup_bytes(tmp_path, monkeypatch):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    _, retained = media_description(root, data)
    result, code = media.capture(args)
    assert code == 0
    item = result["captured"][0]
    assert item["dedup_of"] == "knowledgebase/" + retained.relative_to(root).as_posix()
    assert item["committed"]
    assert len(list((root / "_media").rglob("*.png"))) == 1


@pytest.mark.parametrize("bad", ["missing", "mismatched", "hardlink", "symlink", "duplicate", "malformed", "unowned"])
def test_capture_refuses_false_or_ambiguous_dedup(tmp_path, monkeypatch, bad):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    note, retained = media_description(root, data)
    if bad == "missing":
        retained.unlink()
    elif bad == "mismatched":
        retained.write_bytes(b"wrong image")
    elif bad == "hardlink":
        os.link(retained, tmp_path / "alias.png")
    elif bad == "symlink":
        retained.rename(tmp_path / "original.png")
        retained.symlink_to(tmp_path / "original.png")
    elif bad == "duplicate":
        media_description(root, data, name="duplicate")
    elif bad == "malformed":
        note.write_text(note.read_text().replace("type: media-description", "type: media-description\nbad: a: b"))
    else:
        note.write_text(note.read_text().replace("author: kb-bot", "author: human"))
    result, code = media.capture(args)
    assert code != 0 and result["failed"] and not result["captured"]
    assert not list(root.rglob(".kb-media-*"))


def test_unrelated_malformed_metadata_is_reported_not_used(tmp_path, monkeypatch):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    note, _ = media_description(root, b"unrelated bytes")
    note.write_text(note.read_text().replace("type: media-description", "type: media-description\nbad: a: b"))
    result, code = media.capture(args)
    assert code == 0 and result["warnings"]
    assert result["captured"][0]["dedup_of"] is None


@pytest.mark.parametrize("staged", [False, True])
def test_capture_publication_failure_preserves_destination_and_retry(tmp_path, monkeypatch, staged):
    media, root, args, data = capture_case(tmp_path, monkeypatch, staged=staged)
    publish = media.publish_media_bytes

    def interrupted(path, payload):
        publish(path, payload)
        raise OSError("simulated post-publication failure")

    monkeypatch.setattr(media, "publish_media_bytes", interrupted)
    result, code = media.capture(args)
    assert code != 0 and result["failed"][0]["publication_may_have_succeeded"]
    target = Path(result["failed"][0]["target_path"])
    assert target.read_bytes() == data
    monkeypatch.setattr(media, "publish_media_bytes", publish)
    retry, code = media.capture(args)
    assert code == 0 and retry["captured"][0]["already_present"]


@pytest.mark.parametrize("staged", [False, True])
def test_capture_racing_destination_is_not_overwritten(tmp_path, monkeypatch, staged):
    media, root, args, _ = capture_case(tmp_path, monkeypatch, staged=staged)
    publish = media.publish_media_bytes

    def race(path, data):
        path.write_bytes(b"other writer")
        publish(path, data)

    monkeypatch.setattr(media, "publish_media_bytes", race)
    result, code = media.capture(args)
    assert code != 0
    assert Path(result["failed"][0]["target_path"]).read_bytes() == b"other writer"


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "busy"])
def test_capture_unsafe_or_busy_lock_cannot_publish(tmp_path, monkeypatch, staged, kind):
    media, root, args, _ = capture_case(tmp_path, monkeypatch, staged=staged)
    lock = root / ".kb-write.lock"
    original = tmp_path / "original-lock"
    original.write_bytes(b"unchanged")
    held = None
    if kind == "symlink":
        lock.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, lock)
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        held = lock.open("w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result, code = media.capture(args)
        assert code != 0 and not result["captured"]
        assert not (root / "_media").exists()
        assert not (root / "_tools").exists()
        assert original.read_bytes() == b"unchanged"
    finally:
        if held:
            held.close()


def test_dedup_created_during_fetch_is_rechecked_before_write(tmp_path, monkeypatch):
    media, root, args, data = capture_case(tmp_path, monkeypatch)

    def fetch(*unused):
        media_description(root, data)
        return data, "image/png", None

    monkeypatch.setattr(media, "fetch_bytes", fetch)
    result, code = media.capture(args)
    assert code == 0 and result["captured"][0]["dedup_of"]
    assert len(list((root / "_media").rglob("*.png"))) == 1


def test_body_only_hash_does_not_establish_dedup(tmp_path, monkeypatch):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    note, _ = media_description(root, b"different bytes")
    note.write_text(note.read_text() + f"media_id: {hashlib.sha256(data).hexdigest()}\n")
    result, code = media.capture(args)
    assert code == 0 and not result["captured"][0]["dedup_of"]


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_capture_rejects_unsafe_existing_binary_targets(tmp_path, monkeypatch, staged, kind):
    media, root, args, data = capture_case(tmp_path, monkeypatch, staged=staged)
    first, code = media.capture(args)
    assert code == 0
    item = first["captured"][0]
    target = Path(item["staged_path"]) if staged else root / item["local_path"].removeprefix("knowledgebase/")
    outside = tmp_path / "retained.png"
    outside.write_bytes(data)
    target.unlink()
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    else:
        os.mkfifo(target)
    result, code = media.capture(args)
    assert code != 0 and not result["captured"]
    assert outside.read_bytes() == data


@pytest.mark.parametrize("mime", ["application/x-sh", "audio/ogg"])
def test_unlisted_mime_extension_uses_safe_binary_suffix(tmp_path, monkeypatch, mime):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    args.capture = ["https://example.test/payload"]
    monkeypatch.setattr(media, "fetch_bytes", lambda *a: (data, mime, None))
    result, code = media.capture(args)
    assert code == 0
    assert result["captured"][0]["local_path"].endswith(".bin")


def test_unreadable_identity_is_not_silently_treated_as_unrelated(tmp_path, monkeypatch):
    media, root, args, data = capture_case(tmp_path, monkeypatch)
    note, _ = media_description(root, data)
    note.write_bytes(b"\xff\xfe")
    result, code = media.capture(args)
    assert code != 0 and not result["captured"]


def test_numeric_yaml_media_id_is_not_coerced_to_string(tmp_path):
    media = load_media_module()
    root = tmp_path / "kb"
    root.mkdir()
    note, _ = media_description(root, b"placeholder")
    digest = "1" * 64
    note.write_text("---\nauthor: kb-bot\ntype: media-description\nmedia_status: captured\n"
                    f"media_id: {digest}\n---\n")
    with pytest.raises(ValueError, match="SHA-256 string"):
        media.scan_existing_media(root, digest, b"bytes", 25, [])
