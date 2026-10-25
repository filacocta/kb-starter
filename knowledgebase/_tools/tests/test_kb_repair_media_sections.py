from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-repair-media-sections.py"

FRONTMATTER = """---
author: kb-bot
created: 2026-09-07
modified: 2026-01-01
type: media-description
topic: example
title: Example image
description: An existing captured image note.
status: final
tags: [media]
sources: ["[[knowledgebase/topics/example/sources/source]]"]
related: []
media_id: abc123
media_kind: image
media_status: captured
origin_url: "https://example.test/image.png"
local_path: "knowledgebase/_media/images/2026/09/example.png"
---

"""


def load_module():
    spec = importlib.util.spec_from_file_location(
        "kb_repair_media_sections_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_note(
    tmp_path: Path,
    body: str = "# Example image\n\n## Provenance\n\nExisting text.\n",
    *,
    frontmatter: str = FRONTMATTER,
    name: str = "image.md",
) -> tuple[Path, Path]:
    root = tmp_path / "knowledgebase"
    note = root / "topics" / "example" / "notes" / "media" / name
    note.parent.mkdir(parents=True)
    note.write_text(frontmatter + body, encoding="utf-8")
    return root, note


def run_and_read(module, root: Path, capsys, *extra: str) -> tuple[int, dict]:
    code = module.main(["--kb-root", str(root), *extra])
    output = capsys.readouterr().out
    return code, json.loads(output)


def test_dry_run_is_locked_and_does_not_write(tmp_path: Path, capsys, monkeypatch) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    before = note.read_bytes()
    before_mode = stat.S_IMODE(note.stat().st_mode)

    def unexpected_write(*args, **kwargs):
        raise AssertionError("dry-run called atomic_write")

    monkeypatch.setattr(module._write_guard, "atomic_write", unexpected_write)
    code, result = run_and_read(module, root, capsys)

    assert code == 0
    assert result["applied"] is False
    assert result["repaired"] == [
        {
            "path": "topics/example/notes/media/image.md",
            "added": ["OCR", "Vision Description"],
        }
    ]
    assert note.read_bytes() == before
    assert stat.S_IMODE(note.stat().st_mode) == before_mode
    assert (root / ".kb-write.lock").is_file()


def test_apply_adds_sections_only_and_preserves_frontmatter_body_and_mode(
    tmp_path: Path, capsys
) -> None:
    module = load_module()
    body = (
        "# Example image\n\n"
        "## Provenance\n\nExisting text with trailing spaces.  \n\n"
        "## Interpretation Notes\n\nKeep this text.\n"
    )
    root, note = make_note(tmp_path, body)
    note.chmod(0o640)
    before = note.read_text(encoding="utf-8")
    before_frontmatter, before_body = module._frontmatter_parts(before)

    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 0
    assert result["applied"] is True
    assert result["repaired"][0]["added"] == ["OCR", "Vision Description"]
    after = note.read_text(encoding="utf-8")
    after_frontmatter, after_body = module._frontmatter_parts(after)
    assert after_frontmatter == before_frontmatter
    assert "modified: 2026-01-01" in after_frontmatter
    assert "status: final" in after_frontmatter
    assert "Existing text with trailing spaces.  \n" in after_body
    assert "## OCR\n\n*Pending.*" in after_body
    assert "## Vision Description\n\n*Pending.*" in after_body
    assert after_body.index("## OCR") < after_body.index("## Vision Description")
    assert after_body.index("## Vision Description") < after_body.index("## Interpretation Notes")
    assert stat.S_IMODE(note.stat().st_mode) == 0o640
    assert not list(note.parent.glob(".kb-repair-media-sections-*"))


def test_apply_is_idempotent_and_does_not_rewrite_complete_note(
    tmp_path: Path, capsys
) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    first_code, first = run_and_read(module, root, capsys, "--apply")
    after_first = note.read_bytes()
    first_identity = (note.stat().st_dev, note.stat().st_ino)

    second_code, second = run_and_read(module, root, capsys, "--apply")

    assert first_code == 0 and second_code == 0
    assert first["repaired"]
    assert second["repaired"] == []
    assert note.read_bytes() == after_first
    assert (note.stat().st_dev, note.stat().st_ino) == first_identity


def test_human_note_is_not_a_repair_target(tmp_path: Path, capsys) -> None:
    module = load_module()
    root, note = make_note(
        tmp_path,
        frontmatter=FRONTMATTER.replace("author: kb-bot", "author: human"),
    )
    before = note.read_bytes()

    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 0
    assert result["repaired"] == []
    assert note.read_bytes() == before


def test_duplicate_yaml_is_rejected_without_write(tmp_path: Path, capsys) -> None:
    module = load_module()
    duplicate = FRONTMATTER.replace(
        "author: kb-bot\n", "author: kb-bot\nauthor: human\n"
    )
    root, note = make_note(tmp_path, frontmatter=duplicate)
    before = note.read_bytes()

    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 2
    assert result["repaired"] == []
    assert result["skipped"][0]["path"] == "topics/example/notes/media/image.md"
    assert "invalid YAML" in result["skipped"][0]["reason"]
    assert note.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_note_path_is_never_written(tmp_path: Path, capsys, kind: str) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    outside = tmp_path / f"outside-{kind}.md"
    outside.write_bytes(note.read_bytes())
    note.unlink()
    if kind == "symlink":
        note.symlink_to(outside)
    else:
        os.link(outside, note)
    before = outside.read_bytes()

    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 2
    assert result["repaired"] == []
    assert result["skipped"][0]["path"] == "topics/example/notes/media/image.md"
    assert outside.read_bytes() == before


def test_source_notes_are_outside_the_scan_scope(tmp_path: Path, capsys) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    source = root / "topics" / "example" / "sources" / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text(FRONTMATTER + "# Source\n", encoding="utf-8")
    before = source.read_bytes()

    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 0
    assert result["repaired"] == [
        {
            "path": "topics/example/notes/media/image.md",
            "added": ["OCR", "Vision Description"],
        }
    ]
    assert source.read_bytes() == before


def test_busy_shared_lock_fails_closed_without_write(tmp_path: Path, capsys) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    before = note.read_bytes()

    with module._write_guard.write_lock(root, nonblocking=False):
        code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 2
    assert "another KB writer" in result["error"]
    assert note.read_bytes() == before


def test_symlinked_root_and_unsafe_topic_are_rejected(tmp_path: Path, capsys) -> None:
    module = load_module()
    real_root, note = make_note(tmp_path)
    root_alias = tmp_path / "kb-alias"
    root_alias.symlink_to(real_root, target_is_directory=True)
    before = note.read_bytes()

    code, root_result = run_and_read(module, root_alias, capsys, "--apply")
    assert code == 2
    assert "symlinks" in root_result["error"]
    assert note.read_bytes() == before

    code, topic_result = run_and_read(
        module, real_root, capsys, "--topic", "../example", "--apply"
    )
    assert code == 2
    assert "topic" in topic_result["error"]
    assert note.read_bytes() == before


def test_apply_re_reads_candidate_before_atomic_write(tmp_path: Path, capsys, monkeypatch) -> None:
    module = load_module()
    root, note = make_note(tmp_path)
    original = module._write_guard.regular_text
    reads: list[Path] = []

    def tracked(path):
        candidate = Path(path)
        if candidate == note:
            reads.append(candidate)
        return original(path)

    monkeypatch.setattr(module._write_guard, "regular_text", tracked)
    code, result = run_and_read(module, root, capsys, "--apply")

    assert code == 0
    assert result["repaired"]
    # One plan read + one apply re-read, with the shared atomic writer doing
    # its own checked identity reads as well.
    assert len(reads) >= 2
