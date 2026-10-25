from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from review_fixture import make_review

ROOT = Path(__file__).resolve().parents[2]


def checkoff_module():
    spec = importlib.util.spec_from_file_location("completion_checkoff_test", ROOT / "_tools/kb-checkoff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = """---
author: kb-bot
created: 2026-09-06
modified: 2026-09-06
type: source
topic: test
title: Example source
description: A test-only source record for validation.
status: draft
ingest_status: succeeded
origin: https://example.com/article
tags: []
sources: []
related: []
media: []
---
# Example source

## Source-scoped Record

The source reports an example observation with limited scope and context.

## Evidence Boundary

This test record does not establish broader conclusions beyond the source observation.
"""


def fixture(tmp_path, source=SOURCE, url="https://example.com/article"):
    root = tmp_path / "kb"
    target = root / "topics/test/sources/example.md"
    target.parent.mkdir(parents=True)
    if source is not None:
        target.write_text(source)
    queue = root / "topics/test/reading-list.md"
    queue.write_text("---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n- [ ] " + url + "\n")
    return root, queue, target


def review(root, locator="https://example.com/article"):
    target = root / "topics/test/sources/example.md"
    return make_review(root, target.read_text() if target.exists() else SOURCE,
                       candidate="topics/test/sources/example.md",
                       queue="topics/test/reading-list.md", locator=locator)


def complete(root, queue, **kwargs):
    kwargs.setdefault("review_path", review(root))
    return checkoff_module().checkoff(
        queue,
        "https://example.com/article",
        "x",
        "ingested",
        root / ".kb-write.lock",
        kb_root=root,
        **kwargs,
    )


def test_valid_source_receipt_is_recorded(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, msg = complete(root, queue, source_note="topics/test/sources/example.md")
    assert ok, msg
    assert "- [x] https://example.com/article" in queue.read_text()
    assert "validated-source [[knowledgebase/topics/test/sources/example]] sha256:" in queue.read_text()


@pytest.mark.parametrize("source", [None, SOURCE.replace("succeeded", "failed"), SOURCE.replace("status: draft", "status: deprecated"), SOURCE.replace("https://example.com/article", "https://example.com/other"), SOURCE.replace("## Evidence Boundary", "## Missing"), SOURCE.replace("author: kb-bot", "author: human")])
def test_invalid_source_leaves_item_pending(tmp_path, source):
    root, queue, _ = fixture(tmp_path, source)
    before = queue.read_bytes()
    ok, _ = complete(root, queue, source_note="topics/test/sources/example.md")
    assert not ok
    assert queue.read_bytes() == before


def test_old_unbacked_completion_is_rejected(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, _ = complete(root, queue)
    assert not ok


def test_canonical_note_link_is_backward_compatible(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, msg = checkoff_module().checkoff(queue, "https://example.com/article", "x", "ingested -> [[knowledgebase/topics/test/sources/example]]", root / ".kb-write.lock", kb_root=root, review_path=review(root))
    assert ok, msg


def test_mirror_origin_matches_canonical_queue(tmp_path):
    root, queue, _ = fixture(tmp_path, SOURCE.replace("https://example.com/article", "https://x.com/example/status/123") + "", "https://fxtwitter.com/example/status/123?s=20")
    # The source validator requires transport provenance for mirrored sources.
    target = root / "topics/test/sources/example.md"
    target.write_text(target.read_text().replace("origin:", "mirror: https://api.fxtwitter.com/2/thread/123\norigin:"))
    ok, msg = checkoff_module().checkoff(queue, "https://fxtwitter.com/example/status/123?s=20", "x", "ingested", root / ".kb-write.lock", kb_root=root, source_note="topics/test/sources/example.md", review_path=review(root, "https://fxtwitter.com/example/status/123?s=20"))
    assert ok, msg


def test_nondefault_queue_port_cannot_match_default_source_origin(tmp_path):
    root, queue, _ = fixture(
        tmp_path,
        url="https://example.com:8443/article",
    )
    before = queue.read_bytes()

    ok, message = checkoff_module().checkoff(
        queue,
        "https://example.com:8443/article",
        "x",
        "ingested",
        root / ".kb-write.lock",
        kb_root=root,
        source_note="topics/test/sources/example.md",
        review_path=review(root, "https://example.com:8443/article"),
    )

    assert not ok
    assert "port" in message
    assert queue.read_bytes() == before


def test_nondefault_candidate_origin_is_rejected_before_completion(tmp_path):
    source = SOURCE.replace(
        "https://example.com/article", "https://example.com:8443/article"
    )
    root, queue, _ = fixture(tmp_path, source=source)
    before = queue.read_bytes()

    ok, message = complete(
        root,
        queue,
        source_note="topics/test/sources/example.md",
        review_path=review(root),
    )

    assert not ok
    assert "port" in message
    assert queue.read_bytes() == before


@pytest.mark.parametrize(
    ("source_origin", "queue_url"),
    [
        ("https://example.com:443/article", "https://example.com/article"),
        ("http://example.com:80/article", "https://example.com/article"),
    ],
)
def test_default_source_ports_remain_completion_compatible(
    tmp_path, source_origin, queue_url
):
    source = SOURCE.replace("https://example.com/article", source_origin)
    root, queue, _ = fixture(tmp_path, source=source, url=queue_url)

    ok, message = checkoff_module().checkoff(
        queue,
        queue_url,
        "x",
        "ingested",
        root / ".kb-write.lock",
        kb_root=root,
        source_note="topics/test/sources/example.md",
        review_path=review(root, queue_url),
    )

    assert ok, message
    assert "validated-source" in queue.read_text()


def test_governed_alias_matches_existing_source_origin(tmp_path):
    root, queue, target = fixture(tmp_path, url="https://example.com/legacy")
    target.write_text(target.read_text().replace("https://example.com/article", "https://example.com/current"))
    (root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": "https://example.com/legacy",
                        "source_note": "topics/test/sources/example.md",
                        "evidence": "manual redirect audit",
                        "verified": "2026-09-06",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    ok, msg = checkoff_module().checkoff(
        queue,
        "https://example.com/legacy",
        "x",
        "ingested",
        root / ".kb-write.lock",
        kb_root=root,
        source_note="topics/test/sources/example.md",
        review_path=review(root, "https://example.com/legacy"),
    )
    assert ok, msg
    assert "validated-source" in queue.read_text()


def test_text_repair_cannot_bypass_completion(tmp_path):
    root, queue, _ = fixture(tmp_path)
    before = queue.read_bytes()
    ok, _ = checkoff_module().replace_line_text(queue, "https://example.com/article", "[ ]", "[x]", root / ".kb-write.lock")
    assert not ok
    assert queue.read_bytes() == before


def test_note_cannot_inject_completed_row(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, _ = checkoff_module().checkoff(queue, "https://example.com/article", "!", "reason\n- [x] https://example.com/injected", root / ".kb-write.lock", kb_root=root)
    assert not ok


def test_source_symlink_is_rejected(tmp_path):
    root, queue, target = fixture(tmp_path)
    other = root / "topics/test/sources/other.md"
    other.write_text(target.read_text())
    target.unlink()
    target.symlink_to(other)
    ok, _ = complete(root, queue, source_note="topics/test/sources/example.md")
    assert not ok


def test_source_hardlink_is_rejected(tmp_path):
    root, queue, target = fixture(tmp_path)
    other = root / "topics/test/sources/other.md"
    other.write_text(target.read_text())
    target.unlink()
    os.link(other, target)

    ok, _ = complete(root, queue, source_note="topics/test/sources/example.md")
    assert not ok
    assert "[x]" not in queue.read_text()


def test_source_warning_requires_recorded_reason(tmp_path):
    root, queue, _ = fixture(tmp_path, SOURCE.replace("The source reports an example observation with limited scope and context.", "Brief record."))
    ok, _ = complete(root, queue, source_note="topics/test/sources/example.md")
    assert not ok
    ok, msg = complete(root, queue, source_note="topics/test/sources/example.md", accept_source_warnings="Complete source is two words in this test fixture.")
    assert ok, msg
    assert "warnings-accepted:" in queue.read_text()


def test_source_path_cannot_inject_queue_text(tmp_path):
    root, queue, _ = fixture(tmp_path)
    before = queue.read_bytes()
    ok, _ = complete(root, queue, source_note="topics/test/sources/example\n- [x] injected.md")
    assert not ok
    assert queue.read_bytes() == before


def test_uncheck_removes_completion_receipt(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, msg = complete(root, queue, source_note="topics/test/sources/example.md")
    assert ok, msg
    ok, msg = checkoff_module().checkoff(queue, "https://example.com/article", " ", "repair", root / ".kb-write.lock", kb_root=root, from_mark="x", strip_ingest_note=True)
    assert ok, msg
    assert "validated-source" not in queue.read_text()
    assert "review-receipt" not in queue.read_text()
    assert "- [ ] https://example.com/article" in queue.read_text()


def test_completed_text_repair_does_not_duplicate_receipt(tmp_path):
    root, queue, _ = fixture(tmp_path)
    ok, msg = complete(root, queue, source_note="topics/test/sources/example.md")
    assert ok, msg
    ok, msg = checkoff_module().replace_line_text(queue, "https://example.com/article", "ingested", "reviewed", root / ".kb-write.lock", review_path=review(root))
    assert ok, msg
    assert queue.read_text().count("validated-source") == 1


def test_final_checkoff_rechecks_required_approval_section(tmp_path):
    root, queue, _ = fixture(tmp_path)
    queue.write_text(queue.read_text().replace("## Approved", "## Pending"))
    before = queue.read_bytes()
    ok, msg = complete(root, queue, source_note="topics/test/sources/example.md", required_sections={"Approved", "Auto-approved"})
    assert not ok
    assert "approval section" in msg
    assert queue.read_bytes() == before


def test_direct_checkoff_cannot_replace_review_with_forged_annotation(tmp_path):
    root, queue, target = fixture(tmp_path)
    before = queue.read_bytes()
    ok, message = checkoff_module().checkoff(
        queue, "https://example.com/article", "x",
        "validated-source [[knowledgebase/topics/test/sources/example]] sha256:" + "a" * 64,
        root / ".kb-write.lock", kb_root=root,
        source_note="topics/test/sources/example.md",
    )
    assert not ok
    assert "review-receipt" in message
    assert queue.read_bytes() == before


def test_review_for_different_candidate_bytes_cannot_complete(tmp_path):
    root, queue, target = fixture(tmp_path)
    receipt = review(root)
    target.write_text(SOURCE + "\nDifferent source text.\n")
    before = queue.read_bytes()
    ok, _ = complete(root, queue, source_note="topics/test/sources/example.md", review_path=receipt)
    assert not ok
    assert queue.read_bytes() == before


def test_historical_completed_row_without_review_is_not_retrofitted(tmp_path):
    root, queue, target = fixture(tmp_path)
    queue.write_text(queue.read_text().replace("[ ]", "[x]") + "")
    before = queue.read_bytes()
    ok, _ = checkoff_module().replace_line_text(
        queue, "https://example.com/article", "article", "article",
        root / ".kb-write.lock",
    )
    assert not ok
    assert queue.read_bytes() == before


@pytest.mark.parametrize("sections", [None, {"Pending"}, {"Pending", "Approved"}])
def test_direct_checkoff_cannot_complete_pending_even_with_review(tmp_path, sections):
    root, queue, _ = fixture(tmp_path)
    queue.write_text(queue.read_text().replace("## Approved", "## Pending"))
    before = queue.read_bytes()
    ok, message = complete(root, queue, source_note="topics/test/sources/example.md",
                           required_sections=sections)
    assert not ok
    assert "approval section" in message
    assert queue.read_bytes() == before
