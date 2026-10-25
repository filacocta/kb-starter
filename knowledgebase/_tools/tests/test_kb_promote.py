from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-promote.py"


def load_promote_module():
    spec = importlib.util.spec_from_file_location("kb_promote", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LADDER = """---
author: kb-bot
type: meta
---

# Topic — Reading List

## Pending
- [ ] https://a.example/one — note one
- [ ] https://b.example/two — note two

### Dated sub-group 2026-06-01
- [ ] https://c.example/three — under a sub-heading

## Approved

## Auto-approved
- [ ] https://trusted.example/keep — already trusted

## Processed
- [x] https://done.example/x — ingested

## Failed — Retry
"""


def make_kb(tmp_path: Path) -> tuple[Path, Path]:
    kb_root = tmp_path / "kb"
    rl = kb_root / "topics" / "t" / "reading-list.md"
    rl.parent.mkdir(parents=True)
    rl.write_text(LADDER, encoding="utf-8")
    return kb_root, rl


def section_lines(text: str, section: str) -> list[str]:
    """Item lines under canonical `## section` (sub-groups roll up)."""
    out, canonical = [], None
    import re
    for line in text.splitlines():
        m = re.match(r"^(#{2,3})[ \t]+(.+?)\s*$", line)
        if m:
            if len(m.group(1)) == 2:
                canonical = m.group(2)
            continue
        if canonical == section and re.match(r"^\s*-\s+\[", line):
            out.append(line)
    return out


def call(mod, kb_root, rl, url, frm, to, dry=False):
    return mod.promote(rl, url, frm, to, kb_root / ".kb-write.lock", kb_root, dry_run=dry)


def test_promotion_ignores_fenced_matching_row_and_fake_target(tmp_path):
    mod = load_promote_module()
    root, queue = make_kb(tmp_path)
    example = "```markdown\n## Pending\n- [ ] https://a.example/one\n## Approved\n```\n"
    queue.write_text(LADDER.replace("## Pending\n", example + "## Pending\n", 1))
    ok, message, _ = call(mod, root, queue, "https://a.example/one", "Pending", "Approved")
    assert ok, message
    text = queue.read_text()
    assert example in text
    rows = mod.queue_parser().parse_queue(text).rows
    assert [row.section for row in rows if row.locator == "https://a.example/one"] == ["Approved"]


@pytest.mark.parametrize("fragment", ["## Approved\n", "## approved\n", "```\nunfinished\n"])
def test_promotion_ambiguous_or_unclosed_structure_is_unchanged(tmp_path, fragment):
    mod = load_promote_module()
    root, queue = make_kb(tmp_path)
    text = LADDER + fragment
    queue.write_text(text)
    ok, _, _ = call(mod, root, queue, "https://a.example/one", "Pending", "Approved")
    assert not ok
    assert queue.read_text() == text


def test_promote_moves_line_between_sections(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, info = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Approved")
    assert ok, msg
    text = rl.read_text(encoding="utf-8")
    assert "a.example/one" not in "\n".join(section_lines(text, "Pending"))
    approved = "\n".join(section_lines(text, "Approved"))
    assert "a.example/one" in approved
    # mark preserved as unchecked
    assert "- [ ] https://a.example/one" in text


def test_promote_finds_item_in_sub_group(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    # c.example/three lives under a ### sub-group inside ## Pending
    ok, msg, info = call(mod, kb_root, rl, "https://c.example/three", "Pending", "Auto-approved")
    assert ok, msg
    text = rl.read_text(encoding="utf-8")
    assert "c.example/three" in "\n".join(section_lines(text, "Auto-approved"))
    assert "c.example/three" not in "\n".join(section_lines(text, "Pending"))


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    before = rl.read_text(encoding="utf-8")
    ok, msg, info = call(mod, kb_root, rl, "https://b.example/two", "Pending", "Approved", dry=True)
    assert ok and "dry-run" in msg
    assert rl.read_text(encoding="utf-8") == before


def test_no_match_is_error(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "https://nope.example", "Pending", "Approved")
    assert not ok and "no line" in msg


def test_ambiguous_match_is_error(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    # add a duplicate substring under Pending
    text = rl.read_text(encoding="utf-8").replace(
        "## Approved\n", "## Approved\n", 1
    )
    rl.write_text(text.replace(
        "- [ ] https://b.example/two — note two\n",
        "- [ ] https://b.example/two — note two\n- [ ] https://b.example/two — dup\n",
    ), encoding="utf-8")
    ok, msg, _ = call(mod, kb_root, rl, "https://b.example/two", "Pending", "Approved")
    assert not ok and "ambiguous" in msg


def test_exact_url_does_not_match_prefix_or_annotation(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace(
        "- [ ] https://a.example/one — note one\n",
        "- [ ] https://a.example/one-long — mentions https://a.example/one\n",
    )
    rl.write_text(text, encoding="utf-8")

    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Approved")
    assert not ok and "no line" in msg
    assert "https://a.example/one-long" in rl.read_text(encoding="utf-8")


@pytest.mark.parametrize("mark", ["x", "!", "~", "?"])
def test_checked_or_history_rows_are_not_promoted(tmp_path: Path, mark: str) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace(
        "- [ ] https://a.example/one — note one\n",
        f"- [{mark}] https://a.example/one — note one\n",
    )
    rl.write_text(text, encoding="utf-8")
    before = rl.read_bytes()

    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Approved")
    assert not ok and "no line" in msg
    assert rl.read_bytes() == before


def test_promote_preserves_file_mode(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    rl.chmod(0o664)

    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Approved")
    assert ok, msg
    assert rl.stat().st_mode & 0o777 == 0o664
    assert not list(rl.parent.glob("*.tmp"))


@pytest.mark.parametrize("unsafe", ["human", "symlink", "outside", "source", "nonqueue"])
def test_unsafe_promote_targets_are_unchanged(tmp_path: Path, unsafe: str) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    content = rl.read_text(encoding="utf-8")
    target = rl
    if unsafe == "human":
        target.write_text(content.replace("author: kb-bot", "author: human"), encoding="utf-8")
    elif unsafe == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "outside.md")
        (tmp_path / "outside.md").write_text(content, encoding="utf-8")
    elif unsafe == "outside":
        target = tmp_path / "outside.md"
        target.write_text(content, encoding="utf-8")
    elif unsafe == "source":
        target.write_text(content.replace("type: meta", "type: source"), encoding="utf-8")
    elif unsafe == "nonqueue":
        target = kb_root / "_rules.md"
        target.write_text(content, encoding="utf-8")
    before = target.read_bytes()

    ok, _, _ = call(mod, kb_root, target, "https://a.example/one", "Pending", "Approved")
    assert not ok
    assert target.read_bytes() == before


def test_promote_rejects_symlinked_parent(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    topics = kb_root / "topics"
    alias = topics / "alias"
    alias.symlink_to(rl.parent, target_is_directory=True)
    target = alias / "reading-list.md"
    before = rl.read_bytes()

    ok, _, _ = call(mod, kb_root, target, "https://a.example/one", "Pending", "Approved")
    assert not ok
    assert rl.read_bytes() == before


def test_promote_rejects_parent_traversal(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    target = rl.parent / ".." / rl.parent.name / rl.name
    before = rl.read_bytes()

    ok, _, _ = call(mod, kb_root, target, "https://a.example/one", "Pending", "Approved")
    assert not ok
    assert rl.read_bytes() == before


@pytest.mark.parametrize("url", ["https://a.example injected", "https://a.example\tmeta", "https://a.example\x00meta"])
def test_promote_rejects_url_whitespace_or_control(tmp_path: Path, url: str) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    before = rl.read_bytes()

    ok, msg, _ = call(mod, kb_root, rl, url, "Pending", "Approved")
    assert not ok and "whitespace or control" in msg
    assert rl.read_bytes() == before
def test_missing_target_section_is_error(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace("## Auto-approved\n", "")
    rl.write_text(text, encoding="utf-8")
    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Auto-approved")
    assert not ok and "not found" in msg


def test_noncanonical_sections_rejected(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Frobnicate")
    assert not ok and "canonical" in msg
    ok, msg, _ = call(mod, kb_root, rl, "https://a.example/one", "Pending", "Pending")
    assert not ok and "same section" in msg
