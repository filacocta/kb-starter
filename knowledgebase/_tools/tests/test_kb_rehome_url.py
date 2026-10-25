from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-rehome-url.py"


def load_rehome_module():
    spec = importlib.util.spec_from_file_location("kb_rehome_url", SCRIPT)
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

{pending}
## Approved

## Auto-approved

{auto}
## Processed

{processed}
## Failed — Retry
"""


def make_kb(tmp_path: Path) -> Path:
    kb_root = tmp_path / "kb"
    for topic in ("wrong", "right"):
        rl = kb_root / "topics" / topic / "reading-list.md"
        rl.parent.mkdir(parents=True)
        rl.write_text(LADDER.format(pending="", auto="", processed=""), encoding="utf-8")
    return kb_root


def write_topic(kb_root: Path, topic: str, pending: str = "", auto: str = "", processed: str = "") -> Path:
    rl = kb_root / "topics" / topic / "reading-list.md"
    rl.write_text(LADDER.format(pending=pending, auto=auto, processed=processed), encoding="utf-8")
    return rl


def call(mod, kb_root: Path, **kwargs):
    return mod.rehome(kb_root=kb_root, lock_path=kb_root / ".kb-write.lock", **kwargs)


def section_text(text: str, section: str) -> str:
    current = None
    out = []
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current == section:
            out.append(line)
    return "\n".join(out)


def test_rehome_moves_unchecked_row_between_topics_preserving_section(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    wrong = write_topic(kb_root, "wrong", auto="- [ ] https://example.com/a — note\n")
    right = kb_root / "topics" / "right" / "reading-list.md"

    ok, msg, info = call(
        mod,
        kb_root,
        from_topic="wrong",
        to_topic="right",
        url="https://example.com/a",
    )

    assert ok, msg
    assert "example.com/a" not in wrong.read_text(encoding="utf-8")
    right_text = right.read_text(encoding="utf-8")
    assert "- [ ] https://example.com/a — note" in section_text(right_text, "Auto-approved")
    assert info["from_section"] == "Auto-approved"
    assert info["to_section"] == "Auto-approved"


def test_rehome_can_move_checked_row_to_requested_section_with_note(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    wrong = write_topic(kb_root, "wrong", auto="- [x] https://example.com/done — ingested\n")
    right = kb_root / "topics" / "right" / "reading-list.md"

    ok, msg, info = call(
        mod,
        kb_root,
        from_topic="wrong",
        to_topic="right",
        url="https://example.com/done",
        to_section="Processed",
        note="corrected from wrong topic 2026-06-25",
    )

    assert ok, msg
    assert "example.com/done" not in wrong.read_text(encoding="utf-8")
    processed = section_text(right.read_text(encoding="utf-8"), "Processed")
    assert "- [x] https://example.com/done — ingested — corrected from wrong topic 2026-06-25" in processed
    assert info["to_section"] == "Processed"


def test_rehome_dry_run_does_not_mutate(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    wrong = write_topic(kb_root, "wrong", pending="- [ ] https://example.com/a\n")
    right = kb_root / "topics" / "right" / "reading-list.md"
    before_wrong = wrong.read_text(encoding="utf-8")
    before_right = right.read_text(encoding="utf-8")

    ok, msg, _ = call(
        mod,
        kb_root,
        from_topic="wrong",
        to_topic="right",
        url="https://example.com/a",
        dry_run=True,
    )

    assert ok and "dry-run" in msg
    assert wrong.read_text(encoding="utf-8") == before_wrong
    assert right.read_text(encoding="utf-8") == before_right


def test_rehome_rejects_duplicate_target_url(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    write_topic(kb_root, "wrong", pending="- [ ] https://example.com/a\n")
    write_topic(kb_root, "right", pending="- [ ] https://example.com/a — already here\n")

    ok, msg, _ = call(
        mod,
        kb_root,
        from_topic="wrong",
        to_topic="right",
        url="https://example.com/a",
    )

    assert not ok
    assert "target already contains" in msg


def test_rehome_rejects_ambiguous_source_match(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    write_topic(
        kb_root,
        "wrong",
        pending="- [ ] https://example.com/a — first\n- [ ] https://example.com/a — second\n",
    )

    ok, msg, _ = call(
        mod,
        kb_root,
        from_topic="wrong",
        to_topic="right",
        url="https://example.com/a",
    )

    assert not ok
    assert "ambiguous" in msg


def test_rehome_matches_primary_locator_not_annotation_or_prefix(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    wrong = write_topic(
        kb_root,
        "wrong",
        pending="- [ ] https://example.com/a-long — mentions https://example.com/a\n",
    )
    before = wrong.read_bytes()
    ok, msg, _ = call(mod, kb_root, from_topic="wrong", to_topic="right", url="https://example.com/a")
    assert not ok and "no line" in msg
    assert wrong.read_bytes() == before


def test_rehome_rejects_unsafe_topics_and_multiline_note(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    before = (kb_root / "topics" / "wrong" / "reading-list.md").read_bytes()
    ok, msg, _ = call(mod, kb_root, from_topic="../wrong", to_topic="right", url="https://example.com/a")
    assert not ok and "topic" in msg.lower()
    ok, msg, _ = call(mod, kb_root, from_topic="wrong", to_topic="right", url="https://example.com/a", note="bad\n- [ ] injected")
    assert not ok and "single-line" in msg
    assert (kb_root / "topics" / "wrong" / "reading-list.md").read_bytes() == before


def test_rehome_reports_target_first_partial_state(tmp_path: Path, monkeypatch) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    wrong = write_topic(kb_root, "wrong", pending="- [ ] https://example.com/a\n")
    right = kb_root / "topics" / "right" / "reading-list.md"
    original = mod._atomic_replace
    calls = 0

    def fail_source(path, text, prefix):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated source replacement failure")
        return original(path, text, prefix)

    monkeypatch.setattr(mod, "_atomic_replace", fail_source)
    ok, msg, info = call(mod, kb_root, from_topic="wrong", to_topic="right", url="https://example.com/a")
    assert not ok and "PARTIAL" in msg
    assert info["target_written"] is True
    assert info["source_written"] is False
    assert info["partial"] is True
    assert "https://example.com/a" in wrong.read_text(encoding="utf-8")
    assert "https://example.com/a" in right.read_text(encoding="utf-8")


def test_rehome_ignores_markdown_examples_and_keeps_nested_h3_in_parent_section(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    url = "https://example.com/real"
    examples = (
        "```markdown\n"
        "## Pending\n"
        f"- [ ] {url}\n"
        "```\n"
        f"> - [ ] {url}\n"
        f"    - [ ] {url}\n"
        "<!--\n"
        f"- [ ] {url}\n"
        "-->\n"
        "### subgroup\n"
        f"- [ ] {url}\n"
    )
    source = write_topic(kb_root, "wrong", pending=examples)
    target = kb_root / "topics" / "right" / "reading-list.md"

    ok, msg, info = call(mod, kb_root, from_topic="wrong", to_topic="right", url=url)

    assert ok, msg
    assert info["from_section"] == "Pending"
    source_parsed = mod._queue_parser().parse_queue(source.read_text(encoding="utf-8"))
    target_parsed = mod._queue_parser().parse_queue(target.read_text(encoding="utf-8"))
    assert [row.line for row in source_parsed.rows if row.locator == url] == []
    assert [row.section for row in target_parsed.rows if row.locator == url] == ["Pending"]
    # The example text remains as source prose; only the one semantic row moved.
    assert source.read_text(encoding="utf-8").count(url) == 4


def test_rehome_rejects_duplicate_approval_heading_without_partial_write(tmp_path: Path) -> None:
    mod = load_rehome_module()
    kb_root = make_kb(tmp_path)
    source = write_topic(kb_root, "wrong", pending="- [ ] https://example.com/a\n")
    target = kb_root / "topics" / "right" / "reading-list.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("## Approved\n", "## Approved\n\n## Approved\n", 1),
        encoding="utf-8",
    )
    source_before = source.read_bytes()
    target_before = target.read_bytes()

    ok, msg, _ = call(mod, kb_root, from_topic="wrong", to_topic="right", url="https://example.com/a")

    assert not ok
    assert "duplicate" in msg.lower()
    assert source.read_bytes() == source_before
    assert target.read_bytes() == target_before


def test_rehome_parses_crlf_and_missing_final_newline(tmp_path: Path) -> None:
    mod = load_rehome_module()
    text = LADDER.format(pending="- [ ] https://example.com/crlf\n", auto="", processed="")
    text = text.replace("\n", "\r\n").rstrip("\r\n")

    lines = text.splitlines(keepends=True)
    assert mod.find_rows(lines, "https://example.com/crlf")
    assert mod.section_insert_index(lines, "Pending") is not None


def test_rehome_rejects_unclosed_fence_or_comment_before_any_write(tmp_path: Path) -> None:
    mod = load_rehome_module()
    for marker, label in (("```markdown\n", "fence"), ("<!--\n", "comment")):
        root = tmp_path / label
        root.mkdir()
        kb_root = make_kb(root)
        source = write_topic(
            kb_root,
            "wrong",
            pending=f"- [ ] https://example.com/{label}\n{marker}",
        )
        target = kb_root / "topics" / "right" / "reading-list.md"
        source_before = source.read_bytes()
        target_before = target.read_bytes()

        ok, msg, _ = call(
            mod,
            kb_root,
            from_topic="wrong",
            to_topic="right",
            url=f"https://example.com/{label}",
        )

        assert not ok and "unclosed" in msg.lower()
        assert source.read_bytes() == source_before
        assert target.read_bytes() == target_before
