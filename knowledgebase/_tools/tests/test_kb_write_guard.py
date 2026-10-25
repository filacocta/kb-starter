from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-write-guard.py"
FRONTMATTER = "---\nauthor: kb-bot\ntype: meta\n---\n"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_write_guard_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_queue(tmp_path: Path) -> tuple[object, Path, Path]:
    module = load_module()
    root = tmp_path / "kb"
    queue = root / "readinglist" / "Links.md"
    queue.parent.mkdir(parents=True)
    queue.write_text(FRONTMATTER + "- [ ] https://example.test/item\n", encoding="utf-8")
    return module, root, queue


def test_lock_creates_one_checked_regular_file_without_touch(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    root.mkdir()
    lock = root / ".kb-write.lock"

    with module.write_lock(root, lock):
        assert lock.is_file()
        info = lock.stat()
        assert stat.S_ISREG(info.st_mode)
        assert info.st_nlink == 1

    assert lock.read_bytes() == b""


def test_lock_rejects_symlink_without_touching_destination(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("keep", encoding="utf-8")
    (root / ".kb-write.lock").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        with module.write_lock(root):
            pass
    assert outside.read_text(encoding="utf-8") == "keep"


def test_lock_rejects_hardlink_and_nonregular_file(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("keep", encoding="utf-8")
    os.link(outside, root / ".kb-write.lock")
    with pytest.raises(ValueError, match="multiply-linked"):
        with module.write_lock(root):
            pass

    (root / ".kb-write.lock").unlink()
    os.mkfifo(root / ".kb-write.lock")
    with pytest.raises(ValueError, match="regular"):
        with module.write_lock(root):
            pass


def test_queue_guard_rejects_parent_escape_symlink_and_hardlink(tmp_path: Path) -> None:
    module, root, queue = make_queue(tmp_path)
    assert module.guarded_text(queue, root).startswith("---\n")

    escaped = queue.parent / ".." / "readinglist" / queue.name
    with pytest.raises(ValueError, match="parent traversal"):
        module.guarded_text(escaped, root)

    outside = tmp_path / "outside.md"
    outside.write_text(queue.read_text(encoding="utf-8"), encoding="utf-8")
    queue.unlink()
    queue.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        module.guarded_text(queue, root)

    queue.unlink()
    os.link(outside, queue)
    with pytest.raises(ValueError, match="multiply-linked"):
        module.guarded_text(queue, root)


def test_queue_guard_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    queue = root / "readinglist" / "Links.md"
    queue.parent.mkdir(parents=True)
    os.mkfifo(queue)

    with pytest.raises(ValueError, match="regular"):
        module.guarded_text(queue, root)


def test_queue_guard_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    queue = root / "readinglist" / "Links.md"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        "---\nauthor: human\nauthor: kb-bot\ntype: meta\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid YAML"):
        module.guarded_text(queue, root)


def test_atomic_write_is_unique_and_preserves_mode(tmp_path: Path) -> None:
    module, root, queue = make_queue(tmp_path)
    queue.chmod(0o664)

    module.atomic_write(queue, FRONTMATTER + "updated\n", prefix=".test-write-")

    assert queue.read_text(encoding="utf-8") == FRONTMATTER + "updated\n"
    assert queue.stat().st_mode & 0o777 == 0o664
    assert not list(queue.parent.glob("*.tmp"))
    assert not list(queue.parent.glob(".test-write-*"))


def test_atomic_write_rejects_multiply_linked_target(tmp_path: Path) -> None:
    module, root, queue = make_queue(tmp_path)
    outside = tmp_path / "outside.md"
    os.link(queue, outside)
    before = outside.read_bytes()

    with pytest.raises(ValueError, match="multiply-linked"):
        module.atomic_write(queue, "changed\n")
    assert outside.read_bytes() == before


def test_canonical_root_rejects_symlink(tmp_path: Path) -> None:
    module = load_module()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinks"):
        module.canonical_root(alias)
def test_atomic_create_is_complete_private_and_no_clobber(tmp_path):
    guard = load_module()
    target = tmp_path / "new.md"
    guard.atomic_create(target, FRONTMATTER + "first\n")
    assert target.read_text() == FRONTMATTER + "first\n"
    assert target.stat().st_nlink == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        guard.atomic_create(target, FRONTMATTER + "second\n")
    assert target.read_text() == FRONTMATTER + "first\n"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_atomic_create_refuses_existing_special_target(tmp_path, kind):
    guard = load_module()
    original = tmp_path / "original"
    original.write_text("keep")
    target = tmp_path / "new.md"
    if kind == "symlink":
        target.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, target)
    else:
        os.mkfifo(target)
    with pytest.raises((ValueError, FileExistsError)):
        guard.atomic_create(target, FRONTMATTER)
    assert original.read_text() == "keep"
    assert not list(tmp_path.glob(".kb-create-*"))
