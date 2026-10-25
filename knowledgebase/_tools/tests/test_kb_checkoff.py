from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

FRONTMATTER = "---\nauthor: kb-bot\ntype: meta\n---\n"


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-checkoff.py"


def load_checkoff_module():
    spec = importlib.util.spec_from_file_location("kb_checkoff", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess([], 7, stdout=b"", stderr=b"derive failed"),
        subprocess.TimeoutExpired([], 30),
    ],
)
def test_strict_dashboard_regeneration_reports_subprocess_failure(
    tmp_path: Path, monkeypatch, outcome
) -> None:
    module = load_checkoff_module()

    def fake_run(*args, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="dive-queue regeneration"):
        module.regenerate_dive_queue(tmp_path / "kb", strict=True)


def test_best_effort_dashboard_regeneration_preserves_legacy_nonraising_behavior(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load_checkoff_module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 3, stdout=b"", stderr=b"derive failed"
        ),
    )

    assert module.regenerate_dive_queue(tmp_path / "kb") is False
    assert "warning: dive-queue regeneration exited 3" in capsys.readouterr().err


def make_queue(tmp_path: Path, relative: str, rows: str) -> tuple[Path, Path]:
    root = tmp_path / "kb"
    queue = root / relative
    queue.parent.mkdir(parents=True)
    queue.write_text(FRONTMATTER + rows, encoding="utf-8")
    return root, queue


def test_checkoff_can_route_original_item_to_ideas_backlog(tmp_path: Path) -> None:
    checkoff = load_checkoff_module()
    kb_root = tmp_path / "kb"
    inbox = kb_root / "readinglist" / "topics.md"
    backlog = kb_root / "ideas" / "_backlog.md"
    inbox.parent.mkdir(parents=True)
    backlog.parent.mkdir(parents=True)
    inbox.write_text(
        FRONTMATTER +
        "- [ ] build a tiny workflow dashboard — (via Telegram #inspiration-capture, 2026-06-16)\n",
        encoding="utf-8",
    )
    backlog.write_text(FRONTMATTER + "# Ideas Backlog\n\n## Backlog\n\n", encoding="utf-8")

    ok, msg = checkoff.checkoff(
        inbox,
        "workflow dashboard",
        "x",
        "routed to ideas-backlog 2026-06-16",
        kb_root / ".kb-write.lock",
        ideas_backlog=backlog,
        kb_root=kb_root,
    )

    assert ok is True
    assert "line 5" in msg
    assert "- [x] build a tiny workflow dashboard" in inbox.read_text(encoding="utf-8")
    backlog_text = backlog.read_text(encoding="utf-8")
    assert "- [ ] build a tiny workflow dashboard" in backlog_text
    assert "routed from `readinglist/topics.md` line 5" in backlog_text


def test_checkoff_can_mark_active_item_done(tmp_path: Path) -> None:
    checkoff = load_checkoff_module()
    kb_root = tmp_path / "kb"
    backlog = kb_root / "ideas" / "_backlog.md"
    backlog.parent.mkdir(parents=True)
    backlog.write_text(FRONTMATTER + "- [~] create skills from books\n", encoding="utf-8")

    ok, msg = checkoff.checkoff(
        backlog,
        "skills from books",
        "x",
        "completed with source notes",
        kb_root / ".kb-write.lock",
        kb_root=kb_root,
        from_mark="~",
    )

    assert ok is True
    assert "line 5" in msg
    assert "- [x] create skills from books — completed with source notes" in backlog.read_text(encoding="utf-8")


def test_checkoff_can_repair_uncheck_and_strip_ingest_note(tmp_path: Path) -> None:
    checkoff = load_checkoff_module()
    kb_root = tmp_path / "kb"
    reading_list = kb_root / "topics" / "ai-literacy" / "reading-list.md"
    reading_list.parent.mkdir(parents=True)
    reading_list.write_text(
        FRONTMATTER +
        "- [x] https://fxtwitter.com/example/status/1 — (via Telegram #ai-literacy, 2026-06-28) "
        "— ingested 2026-06-28 -> [[knowledgebase/topics/ai-literacy/sources/bad-batch]]\n",
        encoding="utf-8",
    )

    ok, msg = checkoff.checkoff(
        reading_list,
        "https://fxtwitter.com/example/status/1",
        " ",
        "restored to Auto-approved for source-note repair 2026-06-29",
        kb_root / ".kb-write.lock",
        kb_root=kb_root,
        from_mark="x",
        strip_ingest_note=True,
    )

    assert ok is True
    assert "line 5" in msg
    text = reading_list.read_text(encoding="utf-8")
    assert "- [ ] https://fxtwitter.com/example/status/1" in text
    assert "bad-batch" not in text
    assert "restored to Auto-approved" in text


def test_replace_line_text_updates_unique_queue_line_under_lock(tmp_path: Path) -> None:
    checkoff = load_checkoff_module()
    kb_root = tmp_path / "kb"
    reading_list = kb_root / "topics" / "ai-literacy" / "reading-list.md"
    reading_list.parent.mkdir(parents=True)
    reading_list.write_text(
        FRONTMATTER +
        "- [ ] https://t.co/example — unresolved from [[knowledgebase/topics/ai-literacy/sources/bad-batch]]\n",
        encoding="utf-8",
    )

    ok, msg = checkoff.replace_line_text(
        reading_list,
        "https://t.co/example",
        "[[knowledgebase/topics/ai-literacy/sources/bad-batch]]",
        "[[knowledgebase/topics/ai-literacy/sources/good-source]]",
        kb_root / ".kb-write.lock",
        from_mark=" ",
    )

    assert ok is True
    assert "line 5" in msg


    text = reading_list.read_text(encoding="utf-8")
    assert "bad-batch" not in text
    assert "[[knowledgebase/topics/ai-literacy/sources/good-source]]" in text


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("unsafe", ["outside", "symlink", "human", "malformed", "source", "generated", "nonqueue"])
def test_unsafe_targets_are_unchanged(tmp_path, repair, unsafe):
    module = load_checkoff_module()
    root = tmp_path / "kb"
    root.mkdir()
    (root / "readinglist").mkdir()
    target = root / "readinglist" / "Links.md"
    content = FRONTMATTER + "- [ ] https://example.com/item old\n"
    if unsafe == "outside":
        target = tmp_path / "outside.md"
    elif unsafe == "human":
        content = content.replace("author: kb-bot", "author: human")
    elif unsafe == "malformed":
        content = "---\nauthor: [invalid\n---\n- [ ] https://example.com/item old\n"
    elif unsafe == "source":
        content = content.replace("type: meta", "type: source")
    elif unsafe == "generated":
        target = root / "_dive-queue.md"
    elif unsafe == "nonqueue":
        target = root / "_rules.md"
    target.write_text(content)
    original = target
    if unsafe == "symlink":
        target = root / "alias.md"
        target.symlink_to(original)
    if repair:
        ok, _ = module.replace_line_text(target, "https://example.com/item", "old", "new", root / ".kb-write.lock")
    else:
        ok, _ = module.checkoff(target, "https://example.com/item", "x", "done", root / ".kb-write.lock", kb_root=root)
    assert not ok
    assert original.read_text() == content


def test_invalid_backlog_does_not_check_off_source(tmp_path):
    module = load_checkoff_module()
    root = tmp_path / "kb"
    root.mkdir()
    (root / "readinglist").mkdir()
    (root / "ideas").mkdir()
    queue = root / "readinglist" / "Links.md"
    content = FRONTMATTER + "- [ ] an idea\n"
    queue.write_text(content)
    backlog = root / "ideas" / "_backlog.md"
    backlog.write_text("human notes\n")
    ok, _ = module.checkoff(queue, "an idea", "x", "routed", root / ".kb-write.lock", ideas_backlog=backlog, kb_root=root)
    assert not ok
    assert queue.read_text() == content


def test_backlog_write_failure_leaves_queue_pending(tmp_path, monkeypatch):
    module = load_checkoff_module()
    root = tmp_path / "kb"
    root.mkdir()
    (root / "readinglist").mkdir()
    (root / "ideas").mkdir()
    queue = root / "readinglist" / "Links.md"
    content = FRONTMATTER + "- [ ] an idea\n"
    queue.write_text(content)
    backlog = root / "ideas" / "_backlog.md"
    backlog.write_text(FRONTMATTER)
    def fail(*args):
        raise OSError("simulated storage failure")
    monkeypatch.setattr(module, "append_ideas_backlog", fail)
    with pytest.raises(OSError):
        module.checkoff(queue, "an idea", "x", "routed", root / ".kb-write.lock", ideas_backlog=backlog, kb_root=root)
    assert queue.read_text() == content


def test_checkoff_preserves_file_mode(tmp_path):
    module = load_checkoff_module()
    root = tmp_path / "kb"
    queue = root / "readinglist" / "Links.md"
    queue.parent.mkdir(parents=True)
    queue.write_text(FRONTMATTER + "- [ ] example\n")
    queue.chmod(0o664)
    ok, _ = module.checkoff(queue, "example", "x", "", root / ".kb-write.lock", kb_root=root)
    assert ok
    assert queue.stat().st_mode & 0o777 == 0o664


def test_locator_selector_does_not_match_a_prefix(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [ ] https://example.com/item — captured from the inbox\n",
    )
    before = queue.read_bytes()

    ok, msg = module.checkoff(
        queue,
        "https://example.com/ite",
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )

    assert not ok
    assert "matching selector" in msg
    assert queue.read_bytes() == before


def test_locator_and_prose_selectors_ignore_annotations_in_both_operations(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [ ] https://example.com/item — mentions https://example.com/annotation and captured item\n",
    )
    before = queue.read_bytes()

    ok, _ = module.checkoff(
        queue,
        "https://example.com/annotation",
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )
    assert not ok
    ok, _ = module.replace_line_text(
        queue,
        "https://example.com/annotation",
        "captured item",
        "repaired item",
        root / ".kb-write.lock",
    )
    assert not ok

    # A prose selector must not use an URL-row annotation as a fallback.
    ok, _ = module.checkoff(
        queue,
        "captured item",
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )
    assert not ok
    ok, _ = module.replace_line_text(
        queue,
        "captured item",
        "captured item",
        "repaired item",
        root / ".kb-write.lock",
    )
    assert not ok
    assert queue.read_bytes() == before


def test_exact_locator_selector_handles_other_checkbox_marks(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [!] https://example.com/failed — retry later\n",
    )

    ok, msg = module.checkoff(
        queue,
        "https://example.com/failed",
        "?",
        "reviewed",
        root / ".kb-write.lock",
        kb_root=root,
        from_mark="!",
    )

    assert ok, msg
    assert "- [?] https://example.com/failed" in queue.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "selector"),
    [
        ("readinglist/Links.md", "https://example.com/exact"),
        ("topics/example/reading-list.md", "/tmp/example-source.pdf"),
    ],
)
def test_exact_url_and_absolute_pdf_selectors_match_only_the_primary_token(
    tmp_path: Path,
    relative: str,
    selector: str,
) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(tmp_path, relative, f"- [ ] {selector} — exact locator\n")

    ok, msg = module.checkoff(
        queue,
        selector,
        "!",
        "reviewed",
        root / ".kb-write.lock",
        kb_root=root,
    )

    assert ok, msg
    assert f"- [!] {selector}" in queue.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    ["readinglist/Links.md", "readinglist/topics.md", "ideas/_backlog.md"],
)
def test_unique_prose_selector_remains_supported_in_inbox_and_backlog(
    tmp_path: Path, relative: str
) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        relative,
        "- [ ] build workflow dashboard — captured as prose\n",
    )

    ok, msg = module.checkoff(
        queue,
        "workflow dashboard",
        "x",
        "routed",
        root / ".kb-write.lock",
        kb_root=root,
    )

    assert ok, msg
    assert "- [x] build workflow dashboard" in queue.read_text(encoding="utf-8")


@pytest.mark.parametrize("selector", ["", " ", "https://", "/tmp/not-a-pdf.txt"])
def test_empty_or_malformed_selectors_are_rejected_without_writes(
    tmp_path: Path, selector: str
) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [ ] https://example.com/item — note\n",
    )
    before = queue.read_bytes()

    ok, _ = module.checkoff(
        queue,
        selector,
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )
    assert not ok
    ok, _ = module.replace_line_text(
        queue,
        selector,
        "note",
        "repaired",
        root / ".kb-write.lock",
    )
    assert not ok
    assert queue.read_bytes() == before


def test_duplicate_exact_locator_rows_are_ambiguous_for_mark_and_repair(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [ ] https://example.com/duplicate — first\n"
        "- [ ] https://example.com/duplicate — second\n",
    )
    before = queue.read_bytes()

    ok, msg = module.checkoff(
        queue,
        "https://example.com/duplicate",
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )
    assert not ok
    assert "ambiguous" in msg
    ok, msg = module.replace_line_text(
        queue,
        "https://example.com/duplicate",
        "first",
        "repaired",
        root / ".kb-write.lock",
    )
    assert not ok
    assert "ambiguous" in msg
    assert queue.read_bytes() == before


def test_url_selector_cannot_match_a_prose_row_annotation(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/topics.md",
        "- [ ] consider this source — https://example.com/annotated\n",
    )
    before = queue.read_bytes()

    ok, _ = module.checkoff(
        queue,
        "https://example.com/annotated",
        "x",
        "done",
        root / ".kb-write.lock",
        kb_root=root,
    )

    assert not ok
    assert queue.read_bytes() == before


@pytest.mark.parametrize(
    ("target_section", "trailing_section", "expected"),
    [("Pending", "Approved", False), ("Approved", "Pending", True)],
)
def test_checkoff_uses_selected_row_section_not_trailing_heading(
    tmp_path: Path, monkeypatch, target_section: str, trailing_section: str, expected: bool
) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "topics/example/reading-list.md",
        f"## {target_section}\n"
        "- [ ] https://example.com/selected\n"
        f"## {trailing_section}\n"
        "- [ ] https://example.com/other\n",
    )
    monkeypatch.setattr(module, "source_evidence", lambda *args, **kwargs: "")

    ok, message = module.checkoff(
        queue,
        "https://example.com/selected",
        "x",
        "completed",
        root / ".kb-write.lock",
        kb_root=root,
    )

    assert ok is expected, message
    text = queue.read_text(encoding="utf-8")
    if expected:
        assert "- [x] https://example.com/selected" in text
    else:
        assert "- [ ] https://example.com/selected" in text


def test_checkoff_and_repair_ignore_fenced_duplicate_rows(tmp_path: Path) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "topics/example/reading-list.md",
        "## Approved\n"
        "```markdown\n"
        "## Approved\n"
        "- [x] https://example.com/repair — fake\n"
        "```\n"
        "- [x] https://example.com/repair — real\n",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "source_evidence", lambda *args, **kwargs: "")
    try:
        ok, message = module.checkoff(
            queue,
            "https://example.com/repair",
            " ",
            "restored",
            root / ".kb-write.lock",
            kb_root=root,
            from_mark="x",
            strip_ingest_note=True,
        )
        assert ok, message
        text = queue.read_text(encoding="utf-8")
        assert "- [x] https://example.com/repair — fake" in text
        assert "- [ ] https://example.com/repair — real — restored" in text
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("operation", ["note", "repair"])
def test_checkoff_rechecks_proposed_text_before_writing_comment_injection(
    tmp_path: Path, operation: str
) -> None:
    module = load_checkoff_module()
    root, queue = make_queue(
        tmp_path,
        "readinglist/Links.md",
        "- [ ] build dashboard — captured\n" if operation == "note"
        else "- [x] build dashboard — captured\n",
    )
    before = queue.read_bytes()

    if operation == "note":
        ok, message = module.checkoff(
            queue,
            "build dashboard",
            "x",
            "<!-- hidden",
            root / ".kb-write.lock",
            kb_root=root,
        )
    else:
        ok, message = module.replace_line_text(
            queue,
            "build dashboard",
            "captured",
            "<!-- hidden",
            root / ".kb-write.lock",
        )

    assert not ok, message
    assert queue.read_bytes() == before


@pytest.mark.parametrize("row,selector,old,new", [
    ("- [X] build dashboard", "build dashboard", "build dashboard", "review KB"),
    ("- [ ] https://example.com/old", "https://example.com/old", "/old", "/new"),
])
def test_text_repair_can_change_its_old_selector_without_changing_mark(
    tmp_path, row, selector, old, new
):
    module = load_checkoff_module()
    root, queue = make_queue(tmp_path, "readinglist/Links.md", row + "\n")
    ok, message = module.replace_line_text(queue, selector, old, new, root / ".kb-write.lock")
    assert ok, message
    assert row.replace(old, new) in queue.read_text()
