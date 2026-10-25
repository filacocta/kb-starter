"""Offline adversarial coverage for the ``scripts/kb-fix`` safety boundary."""

from __future__ import annotations

import importlib.util
import importlib.machinery
import os
from pathlib import Path
import stat
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "kb-fix"
LOADER = importlib.machinery.SourceFileLoader("kb_fix_under_test", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None and SPEC.loader is not None
kb_fix = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kb_fix
SPEC.loader.exec_module(kb_fix)


def write_note(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def owned_note(*, title: str = "Example") -> str:
    return (
        "---\n"
        "author: kb-bot\n"
        "type: concept\n"
        f"title: {title}\n"
        "---\n\n"
        "LaTeX: `\\alpha`\n"
    )


def run_cli(monkeypatch: pytest.MonkeyPatch, root: Path, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["kb-fix", "--kb-root", str(root), *args])
    return kb_fix.main()


def test_owned_mutable_note_keeps_math_and_repairs_colon(monkeypatch, tmp_path, capsys):
    path = write_note(
        tmp_path,
        "topics/demo/notes/example.md",
        owned_note(title="Label: value"),
    )

    assert run_cli(monkeypatch, tmp_path, "--fix", "all") == 1
    result = path.read_text(encoding="utf-8")
    assert 'title: "Label: value"' in result
    assert "$$\\alpha$$" in result
    assert "author: kb-bot" in result
    assert "changes applied in-place" in capsys.readouterr().out


@pytest.mark.parametrize(
    "relative,text",
    [
        (
            "topics/demo/notes/no-author.md",
            "---\ntype: concept\ntitle: Missing author\n---\n\nLaTeX: `\\alpha`\n",
        ),
        (
            "topics/demo/notes/no-frontmatter.md",
            "LaTeX: `\\alpha`\n",
        ),
        (
            "topics/demo/notes/human.md",
            "---\nauthor: human\ntype: concept\n---\n\nLaTeX: `\\alpha`\n",
        ),
        (
            "topics/demo/notes/duplicate-author.md",
            "---\nauthor: kb-bot\nauthor: human\ntype: concept\n---\n\nLaTeX: `\\alpha`\n",
        ),
    ],
)
def test_missing_or_ambiguous_ownership_is_never_adopted(
    monkeypatch, tmp_path, relative, text
):
    path = write_note(tmp_path, relative, text)
    before = path.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--fix", "all") == 2
    assert path.read_bytes() == before


def test_retired_ownership_fixers_are_rejected(monkeypatch, tmp_path):
    path = write_note(
        tmp_path,
        "topics/demo/notes/no-author.md",
        "---\ntype: concept\n---\n\nLaTeX: `\\alpha`\n",
    )
    before = path.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--fix", "missing_author") == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "relative,text",
    [
        (
            "topics/demo/sources/source.md",
            owned_note(title="Source: immutable"),
        ),
        (
            "topics/demo/notes/source-type.md",
            "---\nauthor: kb-bot\ntype: source\ntitle: Source: immutable\n---\n\nLaTeX: `\\alpha`\n",
        ),
    ],
)
def test_source_paths_and_source_types_are_immutable(monkeypatch, tmp_path, relative, text):
    path = write_note(tmp_path, relative, text)
    before = path.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--fix", "all") == 2
    assert path.read_bytes() == before


def test_symlink_candidate_is_not_followed_or_changed(monkeypatch, tmp_path):
    target = write_note(tmp_path.parent, "kb-fix-outside.md", owned_note())
    link = tmp_path / "topics/demo/notes/link.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    before = target.read_bytes()
    assert run_cli(monkeypatch, tmp_path, "--fix", "unrendered_math") == 2
    assert link.is_symlink()
    assert target.read_bytes() == before


def test_symlink_root_is_rejected(monkeypatch, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    path = write_note(actual, "topics/demo/notes/example.md", owned_note())
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(actual, target_is_directory=True)
    before = path.read_bytes()

    assert run_cli(monkeypatch, linked_root, "--fix", "unrendered_math") == 2
    assert path.read_bytes() == before


def test_symlink_topic_directory_is_reported_without_following(monkeypatch, tmp_path):
    external = tmp_path / "external"
    path = write_note(external, "notes/example.md", owned_note())
    topics = tmp_path / "topics"
    topics.mkdir()
    topics.joinpath("demo").symlink_to(external, target_is_directory=True)
    before = path.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--fix", "unrendered_math") == 2
    assert path.read_bytes() == before


def test_hardlink_candidate_is_rejected(monkeypatch, tmp_path):
    original = write_note(tmp_path, "topics/demo/notes/original.md", owned_note())
    hardlink = tmp_path / "topics/demo/notes/hardlink.md"
    hardlink.hardlink_to(original)
    before = original.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--fix", "unrendered_math") == 2
    assert original.read_bytes() == before
    assert hardlink.read_bytes() == before
    assert original.stat().st_nlink == 2


def test_out_of_root_candidate_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = write_note(tmp_path, "topics/demo/notes/outside.md", owned_note())
    guard = kb_fix._load_write_guard()

    with pytest.raises(kb_fix.FixSafetyError, match="outside"):
        kb_fix.apply_fixers(
            outside,
            "topics/demo/notes/outside.md",
            ["unrendered_math"],
            dry_run=True,
            kb_root=root,
            write_guard=guard,
        )


def test_topic_traversal_is_rejected_before_scan(monkeypatch, tmp_path):
    outside = write_note(tmp_path.parent, "kb-fix-traversal.md", owned_note())
    before = outside.read_bytes()

    assert run_cli(monkeypatch, tmp_path, "--topic", "../outside", "--fix", "all") == 2
    assert outside.read_bytes() == before


def test_atomic_write_preserves_mode(monkeypatch, tmp_path):
    path = write_note(tmp_path, "topics/demo/notes/mode.md", owned_note())
    os.chmod(path, 0o640)

    assert run_cli(monkeypatch, tmp_path, "--fix", "unrendered_math") == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_recheck_refuses_candidate_changed_during_planning(tmp_path):
    root = tmp_path
    path = write_note(root, "topics/demo/notes/race.md", owned_note())
    guard = kb_fix._load_write_guard()
    original_read = guard.regular_text
    reads = 0

    def mutating_read(candidate):
        nonlocal reads
        value = original_read(candidate)
        reads += 1
        if reads == 1:
            Path(candidate).write_text(value.replace("\\alpha", "\\beta"), encoding="utf-8")
        return value

    class MutatingGuard:
        canonical_root = staticmethod(guard.canonical_root)
        atomic_write = staticmethod(guard.atomic_write)
        regular_text = staticmethod(mutating_read)

    with pytest.raises(kb_fix.FixSafetyError, match="changed during fix planning"):
        kb_fix.apply_fixers(
            path,
            "topics/demo/notes/race.md",
            ["unrendered_math"],
            dry_run=False,
            kb_root=root,
            write_guard=MutatingGuard,
            lock_held=True,
        )
    assert "\\beta" in path.read_text(encoding="utf-8")
    assert "$$\\alpha$$" not in path.read_text(encoding="utf-8")
