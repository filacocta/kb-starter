from __future__ import annotations

import fcntl
import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-derive-dive-queue.py"
FRONTMATTER = "---\nauthor: kb-bot\ntype: meta\nmodified: 2026-09-06\n---\n"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_derive_dive_queue", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "kb"
    topic = root / "topics" / "example"
    topic.mkdir(parents=True)
    (topic / "reading-list.md").write_text(
        FRONTMATTER
        + "# Example — Reading List\n\n"
        + "## Auto-approved\n\n"
        + "- [ ] https://example.test/auto\n\n"
        + "## Processed\n\n"
        + "- [ ] https://example.test/not-active\n",
        encoding="utf-8",
    )
    (topic / "research-ideas.md").write_text(
        FRONTMATTER
        + "# Example — Research Ideas\n\n"
        + "## Ideas\n\n"
        + "- [ ] Investigate the example\n",
        encoding="utf-8",
    )
    target = root / "_dive-queue.md"
    target.write_text(FRONTMATTER + "# Existing dashboard\n", encoding="utf-8")
    return root, target


def test_render_and_write_use_canonical_generated_contract(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    target.chmod(0o664)

    module.write_generated(root)

    text = target.read_text(encoding="utf-8")
    assert "https://example.test/auto" in text
    assert "Investigate the example" in text
    assert "https://example.test/not-active" not in text
    assert target.stat().st_mode & 0o777 == 0o664
    assert not list(root.glob(".kb-dive-queue-*"))
    assert not list(root.glob("*.tmp"))


def test_write_creates_missing_generated_target_without_overwrite(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    target.unlink()

    module.write_generated(root)

    assert target.is_file()
    assert target.stat().st_nlink == 1
    assert "https://example.test/auto" in target.read_text(encoding="utf-8")
    assert not list(root.glob(".kb-dive-queue-*"))


def test_write_renders_after_acquiring_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    guard = module._write_guard
    observed: list[bool] = []
    expected = FRONTMATTER + "# Rendered while locked\n"

    def render_while_locked(render_root: Path) -> str:
        assert render_root == root
        try:
            with guard.write_lock(root, nonblocking=True):
                observed.append(False)
        except guard.LockBusyError:
            observed.append(True)
        return expected

    monkeypatch.setattr(module, "render", render_while_locked)
    module.write_generated(root)

    assert observed == [True]
    assert target.read_text(encoding="utf-8") == expected


def test_environment_flag_cannot_bypass_shared_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    monkeypatch.setenv("KB_DIVE_QUEUE_LOCK_HELD", "1")
    before = target.read_bytes()
    calls: list[Path] = []
    original = module._write_guard.write_lock

    def observed_lock(*args, **kwargs):
        calls.append(Path(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(module._write_guard, "write_lock", observed_lock)
    module.write_generated(root)

    assert calls == [root]
    assert target.read_bytes() != before


@pytest.mark.parametrize("kind", ["human", "duplicate-yaml", "source", "symlink", "hardlink", "fifo"])
def test_unsafe_generated_targets_are_rejected_without_touching_destination(
    tmp_path: Path, kind: str
) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    original = target.read_bytes()
    outside = tmp_path / "outside.md"

    if kind == "human":
        target.write_text(FRONTMATTER.replace("author: kb-bot", "author: human"), encoding="utf-8")
    elif kind == "duplicate-yaml":
        target.write_text(
            "---\nauthor: kb-bot\nauthor: human\ntype: meta\n---\n",
            encoding="utf-8",
        )
    elif kind == "source":
        target.write_text(FRONTMATTER.replace("type: meta", "type: source"), encoding="utf-8")
    elif kind == "symlink":
        outside.write_bytes(original)
        target.unlink()
        target.symlink_to(outside)
    elif kind == "hardlink":
        outside.write_bytes(original)
        target.unlink()
        os.link(outside, target)
    elif kind == "fifo":
        target.unlink()
        os.mkfifo(target)

    before = target.read_bytes() if kind not in {"fifo"} else None

    with pytest.raises(module._write_guard.WriteGuardError):
        module.write_generated(root)

    if kind == "symlink":
        assert target.is_symlink()
        assert outside.read_bytes() == original
    elif kind == "hardlink":
        assert outside.read_bytes() == original
    elif kind == "fifo":
        assert target.is_fifo()
    else:
        assert target.read_bytes() == before


def test_invalid_output_is_rejected_before_atomic_replace(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    before = target.read_bytes()

    with pytest.raises(module._write_guard.WriteGuardError):
        module.write_generated(root, "not frontmatter\n")

    assert target.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_shared_lock_is_rejected_without_touching_target(
    tmp_path: Path, kind: str
) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    lock = root / ".kb-write.lock"
    outside = tmp_path / "outside.lock"
    if kind == "symlink":
        outside.write_bytes(b"keep")
        lock.symlink_to(outside)
    elif kind == "hardlink":
        outside.write_bytes(b"keep")
        os.link(outside, lock)
    else:
        os.mkfifo(lock)
    before = target.read_bytes()

    with pytest.raises(module._write_guard.WriteGuardError):
        module.write_generated(root)

    assert target.read_bytes() == before
    if kind != "fifo":
        assert outside.read_bytes() == b"keep"


def test_regenerate_accepts_only_canonical_caller_lock_descriptor(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    guard = module._write_guard

    with guard.write_lock(root) as lock_fd:
        module.regenerate(root, lock_fd=lock_fd)
        assert "https://example.test/auto" in target.read_text(encoding="utf-8")
        with pytest.raises(BlockingIOError):
            other_fd = os.open(root / ".kb-write.lock", os.O_RDWR)
            try:
                fcntl.flock(other_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(other_fd)


def test_regenerate_rejects_wrong_or_closed_lock_descriptor(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    other_root, _ = make_kb(tmp_path / "other")
    other_fd = os.open(other_root / ".kb-write.lock", os.O_RDWR | os.O_CREAT, 0o600)
    before = target.read_bytes()
    try:
        with pytest.raises(module._write_guard.WriteGuardError, match="shared lock"):
            module.regenerate(root, lock_fd=other_fd)
    finally:
        os.close(other_fd)

    closed_fd = os.open(root / ".kb-write.lock", os.O_RDWR | os.O_CREAT, 0o600)
    os.close(closed_fd)
    with pytest.raises(module._write_guard.WriteGuardError):
        module.regenerate(root, lock_fd=closed_fd)
    assert target.read_bytes() == before


def test_regenerate_under_lock_alias_matches_public_api(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    with module._write_guard.write_lock(root) as lock_fd:
        module.regenerate_under_lock(root, lock_fd)
    assert target.read_text(encoding="utf-8").startswith("---\nauthor: kb-bot\n")


def test_render_rejects_symlinked_topic_directory(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path)
    outside = tmp_path / "outside-topic"
    outside.mkdir()
    (outside / "reading-list.md").write_text(
        FRONTMATTER + "## Auto-approved\n\n- [ ] https://outside.test/leak\n",
        encoding="utf-8",
    )
    (root / "topics" / "external").symlink_to(outside, target_is_directory=True)

    with pytest.raises(module._write_guard.WriteGuardError, match="symlink"):
        module.render(root)


@pytest.mark.parametrize("filename", ["reading-list.md", "research-ideas.md"])
def test_render_rejects_fifo_input_without_blocking(tmp_path: Path, filename: str) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path)
    path = root / "topics" / "example" / filename
    path.unlink()
    os.mkfifo(path)

    with pytest.raises(module._write_guard.WriteGuardError, match="regular"):
        module.render(root)


def test_write_output_keeps_legacy_helper_safe_and_atomic(tmp_path: Path) -> None:
    module = load_module()
    root, target = make_kb(tmp_path)
    replacement = FRONTMATTER + "# Direct helper\n"

    module.write_output(target, replacement)

    assert target.read_text(encoding="utf-8") == replacement
    assert not list(root.glob(".kb-dive-queue-*"))


def test_unchecked_section_items_uses_parser_boundaries_for_examples(tmp_path: Path) -> None:
    module = load_module()
    path = tmp_path / "reading-list.md"
    url = "https://example.test/real"
    path.write_text(
        FRONTMATTER
        + "## Auto-approved\n"
        + "```markdown\n## Auto-approved\n"
        + f"- [ ] https://example.test/fenced\n```\n"
        + f"> - [ ] https://example.test/quoted\n"
        + f"    - [ ] https://example.test/indented\n"
        + "<!--\n"
        + f"- [ ] https://example.test/commented\n-->\n"
        + "### subgroup\n"
        + f"- [ ] {url}\n",
        encoding="utf-8",
    )

    items = module.unchecked_section_items(path, "Auto-approved")

    assert items == [f"- [ ] {url}"]


def test_unchecked_section_items_does_not_derive_from_duplicate_approval_heading(tmp_path: Path) -> None:
    module = load_module()
    path = tmp_path / "reading-list.md"
    path.write_text(
        FRONTMATTER
        + "## Auto-approved\n- [ ] https://example.test/one\n\n"
        + "## Auto-approved\n- [ ] https://example.test/two\n",
        encoding="utf-8",
    )

    assert module.unchecked_section_items(path, "Auto-approved") == []


def test_unchecked_section_items_reports_and_deduplicates_ambiguous_input(tmp_path: Path, capsys) -> None:
    module = load_module()
    path = tmp_path / "reading-list.md"
    path.write_text(
        FRONTMATTER
        + "## Auto-approved\n- [ ] https://example.test/one\n"
        + "```markdown\n- [ ] https://example.test/fake\n",
        encoding="utf-8",
    )

    assert module.unchecked_section_items(path, "Auto-approved") == []
    first = capsys.readouterr().err
    assert str(path) in first
    assert "unclosed `-fence" in first
    assert module.unchecked_section_items(path, "Auto-approved") == []
    assert capsys.readouterr().err == ""


def test_unchecked_section_items_handles_research_ideas_and_crlf_without_final_newline(tmp_path: Path) -> None:
    module = load_module()
    path = tmp_path / "research-ideas.md"
    text = (
        FRONTMATTER
        + "## Ideas\n"
        + "```text\n- [ ] fake in fence\n```\n"
        + "### subgroup\n"
        + "- [ ] real idea"
    )
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    assert module.unchecked_section_items(path, "Ideas") == ["- [ ] real idea"]


def test_research_ideas_collection_spans_category_headings_but_not_examples(tmp_path: Path) -> None:
    module = load_module()
    path = tmp_path / "research-ideas.md"
    path.write_text(
        FRONTMATTER
        + "## Opportunity and Market Mapping\n"
        + "- [ ] First category question\n\n"
        + "## Rights-Cleared Clip Supply\n"
        + "```text\n- [ ] fake fenced question\n```\n"
        + "- [ ] Second category question\n\n"
        + "## Social Buzz Scanner\n"
        + "> - [ ] fake quoted question\n"
        + "    - [ ] fake indented question\n"
        + "<!--\n- [ ] fake commented question\n-->\n"
        + "- [ ] Third category question\n",
        encoding="utf-8",
    )

    assert module.unchecked_section_items(path, "Ideas") == [
        "- [ ] First category question",
        "- [ ] Second category question",
        "- [ ] Third category question",
    ]
