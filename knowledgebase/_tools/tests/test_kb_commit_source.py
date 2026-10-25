from __future__ import annotations

import importlib.util
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from review_fixture import make_review


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-commit-source.py"
URL = "https://example.com/article"


@pytest.mark.parametrize('kind', ['hardlink', 'fifo'])
def test_publisher_rejects_unsafe_lock_without_writes(tmp_path, kind):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    lock = root / '.kb-write.lock'
    marker = tmp_path / 'marker'
    marker.write_text('unchanged')
    if kind == 'hardlink':
        os.link(marker, lock)
    else:
        os.mkfifo(lock)
    before = queue.read_bytes()
    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged)
    assert queue.read_bytes() == before
    assert not target.exists()
    assert marker.read_text() == 'unchanged'


def test_publisher_rejects_symlink_root(tmp_path):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        run_commit(module, alias, queue, staged)
    assert not target.exists()


def test_publisher_rejects_hardlinked_resume_source(tmp_path):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    os.link(staged, target)
    before = queue.read_bytes()
    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged)
    assert queue.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_publisher_rejects_unsafe_staged_input_without_writes(tmp_path, kind):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    outside = tmp_path / "outside-staged.md"
    if kind == "symlink":
        outside.write_bytes(staged.read_bytes())
        staged.unlink()
        staged.symlink_to(outside)
    else:
        os.link(staged, outside)
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="regular, singly-linked"):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()


@pytest.mark.parametrize(
    ("needle", "replacement"),
    [
        ("author: kb-bot", "author: human\nauthor: kb-bot"),
        (
            'origin: "https://example.com/article"',
            'origin: "https://example.com/first"\norigin: "https://example.com/article"',
        ),
        (
            "ingest_status: succeeded",
            "ingest_status: failed\ningest_status: succeeded",
        ),
    ],
)
def test_publisher_rejects_ambiguous_source_identity_without_writes(
    tmp_path, needle, replacement
):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    staged.write_text(
        staged.read_text(encoding="utf-8").replace(needle, replacement, 1),
        encoding="utf-8",
    )
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="identity"):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_approval_scan_builds_identity_index_only_once(tmp_path, monkeypatch):
    module = load_commit_module()
    seen = module.tool('kb-seen')
    calls = []
    def index(root):
        calls.append(root)
        return {}
    monkeypatch.setattr(seen, 'load_index', index)
    text = '## Approved\n' + ''.join(f'- [ ] https://example.org/{n}\n' for n in range(500))
    row, locator = module.approved_row(text, 'https://example.org/499', 'interactive', tmp_path)
    assert locator == 'https://example.org/499'
    assert len(calls) == 1


def test_publisher_reuses_one_identity_index_per_phase(tmp_path: Path, monkeypatch) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    disable_regeneration(module, monkeypatch)
    seen = module.tool("kb-seen")
    original_load_index = seen.load_index
    calls: list[Path] = []

    def counted_load_index(kb_root: Path):
        calls.append(kb_root)
        return original_load_index(kb_root)

    monkeypatch.setattr(seen, "load_index", counted_load_index)

    result = run_commit(module, root, queue, staged)

    assert result["status"] == "SOURCE_AND_QUEUE_COMPLETE"
    assert target.exists()
    assert len(calls) == 2


def test_publisher_rechecks_identity_index_under_lock(tmp_path: Path, monkeypatch) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    disable_regeneration(module, monkeypatch)
    seen = module.tool("kb-seen")
    original_load_index = seen.load_index
    duplicate = root / "topics" / "example" / "sources" / "existing.md"
    calls = 0

    def insert_before_locked_load(kb_root: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            duplicate.write_text(valid_source_text(), encoding="utf-8")
        return original_load_index(kb_root)

    monkeypatch.setattr(seen, "load_index", insert_before_locked_load)
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="origin already exists"):
        run_commit(module, root, queue, staged)

    assert calls == 2
    assert queue.read_bytes() == before
    assert not target.exists()


def load_commit_module():
    spec = importlib.util.spec_from_file_location("kb_commit_source_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_source_text(origin: str = URL, title: str = "Staged Source") -> str:
    return "\n".join(
        [
            "---",
            "author: kb-bot",
            "created: 2026-08-08",
            "modified: 2026-08-08",
            "type: source",
            "topic: example",
            f"title: {title}",
            "description: A staged source candidate.",
            "status: draft",
            "tags: []",
            "sources: []",
            "related: []",
            f'origin: "{origin}"',
            "ingest_status: succeeded",
            "media: []",
            "---",
            "",
            f"# {title}",
            "",
            "## Source-scoped Record",
            "",
            "The source reports a concrete result and names the underlying material.",
            "",
            "## Evidence Boundary",
            "",
            "This captures the source's report and does not independently validate it.",
            "",
        ]
    )


def make_case(tmp_path: Path, section: str = "Approved", url: str = URL):
    root = tmp_path / "knowledgebase"
    source_dir = root / "topics" / "example" / "sources"
    source_dir.mkdir(parents=True)
    queue = root / "topics" / "example" / "reading-list.md"
    queue.write_text(
        "\n".join(
            [
                "---",
                "author: kb-bot",
                "type: meta",
                "---",
                f"## {section}",
                "",
                f"- [ ] {url}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    staged = tmp_path / "staged.md"
    staged.write_text(valid_source_text(url), encoding="utf-8")
    target = source_dir / "new.md"
    return root, queue, staged, target


def disable_regeneration(module, monkeypatch):
    checkoff = module.tool("kb-checkoff")
    monkeypatch.setattr(checkoff, "regenerate_dive_queue", lambda root: None)
    return checkoff


def run_commit(module, root, queue, staged, *, mode="interactive", **kwargs):
    kwargs.setdefault("review_path", make_review(root, staged.read_text(encoding="utf-8")))
    return module.commit_source(
        root,
        staged,
        "topics/example/sources/new.md",
        queue,
        URL,
        mode=mode,
        **kwargs,
    )


def test_invalid_staged_text_writes_nothing(tmp_path: Path) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    before = queue.read_bytes()
    staged.write_text("---\nauthor: kb-bot\ntype: source\n---\n# incomplete\n", encoding="utf-8")

    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


@pytest.mark.parametrize(
    ("mode", "section", "allowed"),
    [
        ("interactive", "Approved", True),
        ("interactive", "Auto-approved", True),
        ("headless", "Auto-approved", True),
        ("headless", "Approved", False),
    ],
)
def test_approval_sections_follow_mode(tmp_path: Path, monkeypatch, mode, section, allowed) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path, section=section)
    disable_regeneration(module, monkeypatch)

    if allowed:
        result = run_commit(module, root, queue, staged, mode=mode)
        assert result["status"] == "SOURCE_AND_QUEUE_COMPLETE"
        assert target.read_text(encoding="utf-8") == staged.read_text(encoding="utf-8")
        assert "[x]" in queue.read_text(encoding="utf-8")
    else:
        before = queue.read_bytes()
        with pytest.raises(ValueError, match="approved ingestion section"):
            run_commit(module, root, queue, staged, mode=mode)
        assert queue.read_bytes() == before
        assert not target.exists()
        assert not (root / ".kb-write.lock").exists()


def test_only_topic_reading_lists_are_publish_queues(tmp_path: Path) -> None:
    module = load_commit_module()
    root, _, staged, target = make_case(tmp_path)
    global_queue = root / "readinglist" / "Links.md"
    global_queue.parent.mkdir()
    global_queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n- [ ] " + URL + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="topics/<topic>/reading-list"):
        run_commit(module, root, global_queue, staged)

    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"url": URL + "\ninjected"},
        {"accept_warnings": "approved\ninjected"},
    ],
)
def test_single_line_arguments_are_rejected_before_writes(tmp_path: Path, bad_kwargs) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    before = queue.read_bytes()
    kwargs = dict(bad_kwargs)
    bad_url = kwargs.pop("url", URL)

    with pytest.raises(ValueError, match="single-line"):
        module.commit_source(
            root,
            staged,
            "topics/example/sources/new.md",
            queue,
            bad_url,
            mode="interactive",
            **kwargs,
        )

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_no_overwrite_of_existing_source(tmp_path: Path) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    existing = valid_source_text("https://example.com/other", "Existing Source")
    target.write_text(existing, encoding="utf-8")
    before_queue = queue.read_bytes()

    with pytest.raises(ValueError, match="will not be overwritten"):
        run_commit(module, root, queue, staged)

    assert target.read_text(encoding="utf-8") == existing
    assert queue.read_bytes() == before_queue


def test_successful_publish_records_dated_validated_receipt(tmp_path: Path, monkeypatch) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    disable_regeneration(module, monkeypatch)

    result = run_commit(module, root, queue, staged)
    queue_text = queue.read_text(encoding="utf-8")

    assert result["status"] == "SOURCE_AND_QUEUE_COMPLETE"
    assert result["queue_completed"] is True
    assert target.read_text(encoding="utf-8") == staged.read_text(encoding="utf-8")
    assert re.search(r"ingested \d{4}-\d{2}-\d{2}", queue_text)
    assert "validated-source [[knowledgebase/topics/example/sources/new]] sha256:" in queue_text


def test_duplicate_successful_origin_is_rejected_without_writes(tmp_path: Path) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    duplicate = root / "topics" / "example" / "sources" / "existing.md"
    duplicate.write_text(valid_source_text(), encoding="utf-8")
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="origin already exists"):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_duplicate_scan_rejects_unsafe_source_before_read(
    tmp_path: Path, monkeypatch, kind: str
) -> None:
    module = load_commit_module()
    root, _queue, _staged, target = make_case(tmp_path)
    outside = tmp_path / "outside-source.md"
    outside.write_text(valid_source_text("https://example.com/other"), encoding="utf-8")
    unsafe = root / "topics" / "example" / "sources" / "unsafe.md"
    if kind == "symlink":
        unsafe.symlink_to(outside)
    else:
        os.link(outside, unsafe)

    read_paths: list[Path] = []
    def unexpected_read(path: Path, *args, **kwargs):
        read_paths.append(path)
        raise AssertionError("source index used an unguarded text read")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    with pytest.raises(ValueError, match="symlink|linked|singly"):
        module.duplicate_successful_origin(root, [URL], target)
    assert read_paths == []


def test_publisher_rejects_nondefault_candidate_origin_without_writes(tmp_path: Path) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    staged.write_text(
        valid_source_text("https://example.com:8443/article"), encoding="utf-8"
    )
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="port"):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_duplicate_successful_mirror_is_rejected_without_writes(tmp_path: Path) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    duplicate = root / "topics" / "example" / "sources" / "existing.md"
    duplicate.write_text(
        valid_source_text("https://example.com/other").replace(
            'origin: "https://example.com/other"',
            'origin: "https://example.com/other"\nmirror: "' + URL + '"',
        ),
        encoding="utf-8",
    )
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="origin already exists"):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_duplicate_publish_through_governed_alias_is_blocked_without_writes(tmp_path: Path) -> None:
    module = load_commit_module()
    legacy = "https://example.com/legacy"
    root, queue, staged, target = make_case(tmp_path, url=legacy)
    existing = root / "topics" / "example" / "sources" / "existing.md"
    existing.write_text(valid_source_text("https://example.com/current", "Existing Source"), encoding="utf-8")
    (root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": legacy,
                        "source_note": "topics/example/sources/existing.md",
                        "evidence": "manual redirect audit",
                        "verified": "2026-09-06",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = queue.read_bytes()

    with pytest.raises(ValueError, match="origin already exists"):
        module.commit_source(
            root,
            staged,
            "topics/example/sources/new.md",
            queue,
            legacy,
        )

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_partial_queue_failure_is_explicit_and_identical_retry_completes(tmp_path: Path, monkeypatch) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    checkoff = disable_regeneration(module, monkeypatch)
    real_checkoff = checkoff.checkoff
    monkeypatch.setattr(checkoff, "checkoff", lambda *args, **kwargs: (False, "simulated queue race"))

    partial = run_commit(module, root, queue, staged)
    published = target.read_bytes()

    assert partial["status"] == "SOURCE_WRITTEN_QUEUE_PENDING"
    assert partial["source_written"] is True
    assert partial["queue_completed"] is False
    assert "simulated queue race" in partial["message"]
    assert "- [ ] " + URL in queue.read_text(encoding="utf-8")

    monkeypatch.setattr(checkoff, "checkoff", real_checkoff)
    complete = run_commit(module, root, queue, staged)
    retry = run_commit(module, root, queue, staged)

    assert complete["status"] == "SOURCE_AND_QUEUE_COMPLETE"
    assert retry["status"] == "SOURCE_AND_QUEUE_COMPLETE"
    assert retry["resumed"] is True
    assert target.read_bytes() == published
    assert "- [x] " + URL in queue.read_text(encoding="utf-8")
    assert queue.read_text(encoding="utf-8").count("validated-source") == 1


def test_checkoff_exception_after_publish_is_partial(tmp_path: Path, monkeypatch) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    checkoff = disable_regeneration(module, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("simulated lock failure")

    monkeypatch.setattr(checkoff, "checkoff", fail)
    result = run_commit(module, root, queue, staged)

    assert result["status"] == "SOURCE_WRITTEN_QUEUE_PENDING"
    assert result["source_written"] is True
    assert result["queue_completed"] is False
    assert result["error"] == "simulated lock failure"
    assert target.exists()
    assert "- [ ] " + URL in queue.read_text(encoding="utf-8")


def test_main_reports_malformed_queue_as_stop_without_publish(tmp_path: Path, capsys) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    queue.write_text("---\nauthor: [broken\n---\n- [ ] " + URL + "\n", encoding="utf-8")

    code = module.main(
        [
            "--kb-root",
            str(root),
            "--staged-text",
            str(staged),
            "--candidate-path",
            "topics/example/sources/new.md",
            "--queue-file",
            str(queue),
            "--url",
            URL,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["status"] == "STOP"
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()


def test_approval_revoked_between_publication_and_checkoff_stays_pending(tmp_path, monkeypatch):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    checkoff = disable_regeneration(module, monkeypatch)
    original = checkoff.checkoff

    def revoke_then_check(*args, **kwargs):
        queue.write_text(queue.read_text().replace("## Approved", "## Pending"))
        return original(*args, **kwargs)

    monkeypatch.setattr(checkoff, "checkoff", revoke_then_check)
    result = run_commit(module, root, queue, staged)
    assert result["status"] == "SOURCE_WRITTEN_QUEUE_PENDING"
    assert target.exists()
    assert "- [ ] " + URL in queue.read_text()
    assert "[x]" not in queue.read_text()


def test_publication_requires_review_before_any_source_write(tmp_path):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    before = queue.read_bytes()
    with pytest.raises(ValueError, match="review-receipt"):
        run_commit(module, root, queue, staged, review_path=None)
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()
    assert queue.read_bytes() == before


def test_changed_candidate_cannot_reuse_review(tmp_path):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    review_path = make_review(root, staged.read_text())
    staged.write_text(staged.read_text() + "\nDifferent draft bytes.\n")
    before = queue.read_bytes()
    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged, review_path=review_path)
    assert not target.exists()
    assert queue.read_bytes() == before


def test_review_is_revalidated_under_publication_lock(tmp_path, monkeypatch):
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    review_path = make_review(root, staged.read_text())
    guard = module.tool("kb-write-guard")
    original_lock = guard.write_lock
    before = queue.read_bytes()

    @contextmanager
    def changed_review(*args, **kwargs):
        review_path.write_text(review_path.read_text().replace("fixture-reviewer", "changed-reviewer"))
        with original_lock(*args, **kwargs) as descriptor:
            yield descriptor

    monkeypatch.setattr(guard, "write_lock", changed_review)
    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged, review_path=review_path)
    assert not target.exists()
    assert queue.read_bytes() == before


@pytest.mark.parametrize(
    "queue_body",
    [
        (
            "## Pending\n"
            "```markdown\n"
            "## Approved\n"
            f"- [ ] {URL}\n"
            "```\n"
        ),
        (
            "## Approved\n"
            f"- [ ] {URL}\n"
            "## Approved\n"
        ),
        (
            "## approved\n"
            f"- [ ] {URL}\n"
        ),
    ],
)
def test_publication_rejects_nonsemantic_or_ambiguous_approval_before_writes(
    tmp_path: Path, queue_body: str
) -> None:
    module = load_commit_module()
    root, queue, staged, target = make_case(tmp_path)
    queue.write_text("---\nauthor: kb-bot\ntype: meta\n---\n" + queue_body, encoding="utf-8")
    before = queue.read_bytes()

    with pytest.raises(ValueError):
        run_commit(module, root, queue, staged)

    assert queue.read_bytes() == before
    assert not target.exists()
    assert not (root / ".kb-write.lock").exists()
