from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-promote.py"


def load_promote_module():
    spec = importlib.util.spec_from_file_location("kb_promote", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LADDER = """---
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


def test_promote_moves_line_between_sections(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, info = call(mod, kb_root, rl, "a.example/one", "Pending", "Approved")
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
    ok, msg, info = call(mod, kb_root, rl, "c.example/three", "Pending", "Auto-approved")
    assert ok, msg
    text = rl.read_text(encoding="utf-8")
    assert "c.example/three" in "\n".join(section_lines(text, "Auto-approved"))
    assert "c.example/three" not in "\n".join(section_lines(text, "Pending"))


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    before = rl.read_text(encoding="utf-8")
    ok, msg, info = call(mod, kb_root, rl, "b.example/two", "Pending", "Approved", dry=True)
    assert ok and "dry-run" in msg
    assert rl.read_text(encoding="utf-8") == before


def test_no_match_is_error(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "nope.example", "Pending", "Approved")
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
    ok, msg, _ = call(mod, kb_root, rl, "b.example/two", "Pending", "Approved")
    assert not ok and "ambiguous" in msg


def test_missing_target_section_is_error(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace("## Auto-approved\n", "")
    rl.write_text(text, encoding="utf-8")
    ok, msg, _ = call(mod, kb_root, rl, "a.example/one", "Pending", "Auto-approved")
    assert not ok and "not found" in msg


def test_noncanonical_sections_rejected(tmp_path: Path) -> None:
    mod = load_promote_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "a.example/one", "Pending", "Frobnicate")
    assert not ok and "canonical" in msg
    ok, msg, _ = call(mod, kb_root, rl, "a.example/one", "Pending", "Pending")
    assert not ok and "same section" in msg
