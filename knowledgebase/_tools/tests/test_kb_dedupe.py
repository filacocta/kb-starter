from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-dedupe.py"


def load_dedupe_module():
    spec = importlib.util.spec_from_file_location("kb_dedupe", SCRIPT)
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
- [ ] https://mindat.org/ — (via agent, 2026-06-21)

## Approved

## Auto-approved
- [!] https://mindat.org/ — (via Telegram, 403 permanent headless fail)

## Processed

## Failed — Retry
"""


def make_kb(tmp_path: Path) -> tuple[Path, Path]:
    kb_root = tmp_path / "kb"
    rl = kb_root / "topics" / "t" / "reading-list.md"
    rl.parent.mkdir(parents=True)
    rl.write_text(LADDER, encoding="utf-8")
    return kb_root, rl


def call(mod, kb_root, rl, url, drop, into=None, fold=False, dry=False):
    return mod.dedupe(rl, url, drop, into, fold, kb_root / ".kb-write.lock", kb_root, dry_run=dry)


def test_removes_cross_section_duplicate_and_folds_note(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, info = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved", into="Pending", fold=True)
    assert ok, msg
    text = rl.read_text(encoding="utf-8")
    # exactly one mindat row remains, and it is under Pending with the folded note
    assert text.count("https://mindat.org/") == 1
    assert "## Auto-approved\n\n" in text or "## Auto-approved\n" in text
    assert "deduped from Auto-approved" in text
    assert "403 permanent headless fail" in text  # provenance preserved
    assert "/ —" not in text.split("mindat.org/", 1)[1][:5]  # no URL-remnant artifact


def test_refuses_to_remove_last_copy(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    # remove the Auto-approved dup first
    call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved", into="Pending", fold=True)
    # now only the Pending copy exists — dropping it must be refused
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Pending")
    assert not ok and "last copy" in msg


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    before = rl.read_text(encoding="utf-8")
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved", dry=True)
    assert ok and "dry-run" in msg
    assert rl.read_text(encoding="utf-8") == before


def test_noncanonical_drop_rejected(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Frobnicate")
    assert not ok and "canonical" in msg


def test_missing_row_in_drop_section_is_error(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Approved")
    assert not ok and "no line" in msg


def test_dedupe_matches_primary_locator_not_annotation_or_prefix(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace(
        "- [ ] https://mindat.org/ — (via agent, 2026-06-21)",
        "- [ ] https://mindat.org/long — mentions https://mindat.org/",
    )
    rl.write_text(text, encoding="utf-8")
    before = rl.read_bytes()
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved")
    assert not ok and "last copy" in msg
    assert rl.read_bytes() == before


def test_dedupe_rejects_human_or_noncanonical_path(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    human = rl.read_text(encoding="utf-8").replace("author: kb-bot", "author: human")
    rl.write_text(human, encoding="utf-8")
    before = rl.read_bytes()
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved")
    assert not ok and "author" in msg
    assert rl.read_bytes() == before

    rl.write_text(LADDER, encoding="utf-8")
    traversal = rl.parent / ".." / rl.parent.name / rl.name
    before = rl.read_bytes()
    ok, msg, _ = call(mod, kb_root, traversal, "https://mindat.org/", "Auto-approved")
    assert not ok and "traversal" in msg.lower()
    assert rl.read_bytes() == before


def test_dedupe_preserves_mode_and_rejects_multiline_locator(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    rl.chmod(0o640)
    before = rl.read_bytes()
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/\ninjected", "Auto-approved")
    assert not ok and "single-line" in msg
    assert rl.read_bytes() == before
    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved", into="Pending", fold=True)
    assert ok, msg
    assert rl.stat().st_mode & 0o777 == 0o640
    assert not list(rl.parent.glob("*.tmp"))


def test_rows_by_section_ignores_fences_comments_quotes_and_indented_examples(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    url = "https://example.com/duplicate"
    text = (
        "---\nauthor: kb-bot\ntype: meta\n---\n"
        "## Pending\n"
        f"- [ ] {url} — keeper\n"
        "```markdown\n"
        "## Auto-approved\n"
        f"- [ ] {url} — fenced\n"
        "```\n"
        f"> - [ ] {url} — quoted\n"
        f"    - [ ] {url} — indented\n"
        "<!--\n"
        f"- [ ] {url} — commented\n"
        "-->\n"
        "## Auto-approved\n"
        f"- [ ] {url} — actual duplicate\n"
    )
    rl.write_text(text, encoding="utf-8")

    rows = mod.rows_by_section(text.splitlines(keepends=True), url)

    assert set(rows) == {"Pending", "Auto-approved"}
    assert len(rows["Pending"]) == 1
    assert len(rows["Auto-approved"]) == 1


def test_dedupe_rejects_duplicate_approval_heading_without_write(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    kb_root, rl = make_kb(tmp_path)
    text = rl.read_text(encoding="utf-8").replace(
        "## Auto-approved\n", "## Auto-approved\n\n## Auto-approved\n", 1
    )
    rl.write_text(text, encoding="utf-8")
    before = rl.read_bytes()

    ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved")

    assert not ok
    assert "duplicate" in msg.lower()
    assert rl.read_bytes() == before


def test_dedupe_parses_crlf_and_missing_final_newline(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    _, rl = make_kb(tmp_path)
    text = rl.read_bytes().replace(b"\n", b"\r\n").rstrip(b"\r\n").decode("utf-8")

    rows = mod.rows_by_section(text.splitlines(keepends=True), "https://mindat.org/")

    assert set(rows) == {"Pending", "Auto-approved"}
    assert mod.note_after_url("- [ ] https://mindat.org/\r\n", "https://mindat.org/") == ""


def test_dedupe_rejects_unclosed_fence_or_comment_before_any_write(tmp_path: Path) -> None:
    mod = load_dedupe_module()
    for marker, label in (("```markdown\n", "fence"), ("<!--\n", "comment")):
        root = tmp_path / label
        root.mkdir()
        kb_root, rl = make_kb(root)
        rl.write_text(
            LADDER.replace(
                "## Auto-approved\n",
                f"## Auto-approved\n- [ ] https://mindat.org/\n{marker}",
                1,
            ),
            encoding="utf-8",
        )
        before = rl.read_bytes()

        ok, msg, _ = call(mod, kb_root, rl, "https://mindat.org/", "Auto-approved")

        assert not ok and "unclosed" in msg.lower()
        assert rl.read_bytes() == before
