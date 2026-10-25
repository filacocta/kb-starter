from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-review-receipt.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_review_receipt_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_text(origin: str = "https://example.com/article") -> str:
    return "\n".join(
        [
            "---",
            "author: kb-bot",
            "type: source",
            "topic: example",
            f"origin: {origin}",
            "---",
            "# Example source",
            "",
        ]
    )


def make_case(tmp_path: Path, module, **overrides):
    root = tmp_path / "kb"
    (root / "topics/example/sources").mkdir(parents=True)
    queue = root / "topics/example/reading-list.md"
    queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n"
        "## Approved\n\n- [ ] https://example.com/article\n",
        encoding="utf-8",
    )
    text = candidate_text()
    record = {
        "author": "kb-bot",
        "version": 1,
        "candidate_path": "topics/example/sources/new.md",
        "candidate_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "queue_path": "topics/example/reading-list.md",
        "primary_locator": "https://example.com/article",
        "evidence": [],
        "coverage": {
            "status": "partial",
            "covered": ["The synthetic candidate text"],
            "omissions": ["No external source was retained"],
        },
        "reproducible_snapshot": False,
        "retention_limitations": "Synthetic fixture; no source extraction retained.",
        "writer_ref": "writer-1",
        "reviewer_ref": "reviewer-1",
        "reviewed_at": "2026-09-07T12:00:00Z",
        "disposition": "publish-with-boundary",
    }
    record.update(overrides)
    stage = root / "_tools/staged-review.json"
    stage.parent.mkdir(parents=True)
    stage.write_bytes(module.encoded(record))
    return root, stage, record, text


def retain_case(module, root, stage):
    return module.retain(root, stage)


def verify_case(module, root, path, record, text):
    return module.verify_review(
        root,
        path,
        candidate_path=record["candidate_path"],
        candidate_text=text,
        queue_path=record["queue_path"],
        primary_locator=record["primary_locator"],
    )


def add_evidence(module, root, *, origin="https://example.com/article", scope="supplied-extraction"):
    retain = module._retain()
    source = root / "_tools/extraction.txt"
    source.write_text("Retained synthetic extraction.\n", encoding="utf-8")
    metadata = {
        "origin": origin,
        "transport": origin,
        "retrieved_at": "2026-09-07T12:00:00Z",
        "channel": "page-text" if scope == "supplied-extraction" else "excerpt",
        "scope": scope,
        "limitations": "Synthetic fixture only.",
        "approval_ref": "test approval",
        "retention_basis": "user-provided" if scope == "supplied-extraction" else "permitted-excerpt",
    }
    result = retain.retain(root, source, approved=True, **metadata)
    return {
        "path": result["path"],
        "envelope_sha256": result["sha256"],
        "content_sha256": result["content_sha256"],
    }


def test_retain_verify_and_idempotence_without_overwrite(tmp_path):
    module = load_module()
    root, stage, record, text = make_case(tmp_path, module)
    first = retain_case(module, root, stage)
    target = root / first["path"]
    before = target.read_bytes()
    mtime = target.stat().st_mtime_ns

    second = retain_case(module, root, stage)
    assert first["status"] == "REVIEW_RETAINED"
    assert second["status"] == "REVIEW_ALREADY_RETAINED"
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == mtime
    assert verify_case(module, root, target, record, text) == first["sha256"]


def test_existing_digest_target_is_never_overwritten(tmp_path):
    module = load_module()
    root, stage, _, _ = make_case(tmp_path, module)
    record, canonical = module._validate_staged(root, stage)
    target = root / module.STORE / (module.digest(canonical) + ".json")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"tampered")

    with pytest.raises(ValueError):
        retain_case(module, root, stage)
    assert target.read_bytes() == b"tampered"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_path", "topics/example/not-sources/new.md"),
        ("queue_path", "topics/example/other.md"),
        ("writer_ref", "reviewer-1"),
    ],
)
def test_schema_and_publication_gates_reject_invalid_reviews(tmp_path, field, value):
    module = load_module()
    root, stage, _, _ = make_case(tmp_path, module, **{field: value})
    with pytest.raises(ValueError):
        retain_case(module, root, stage)
    assert not (root / "_tools/reviews").exists()


@pytest.mark.parametrize(
    "locator",
    ["https://example.com:8443/article", "https://example.com:bad/article"],
)
def test_review_receipt_rejects_nondefault_or_malformed_ports(tmp_path, locator):
    module = load_module()
    root, stage, _, _ = make_case(tmp_path, module, primary_locator=locator)

    with pytest.raises(ValueError, match="port"):
        retain_case(module, root, stage)


def test_review_binding_rejects_nondefault_candidate_origin(tmp_path):
    module = load_module()
    root, stage, record, _ = make_case(tmp_path, module)
    nondefault_text = candidate_text("https://example.com:8443/article")
    record["candidate_sha256"] = hashlib.sha256(nondefault_text.encode()).hexdigest()
    stage.write_bytes(module.encoded(record))

    result = retain_case(module, root, stage)
    with pytest.raises(ValueError, match="port"):
        verify_case(module, root, root / result["path"], record, nondefault_text)


@pytest.mark.parametrize(
    "field_value",
    [
        ("coverage", {"status": "partial", "covered": ["x"], "omissions": []}),
        ("evidence", []),
    ],
)
def test_publish_partial_requires_omissions_but_empty_evidence_is_allowed(
    tmp_path, field_value
):
    module = load_module()
    field, value = field_value
    root, stage, _, _ = make_case(tmp_path, module, **{field: value})
    if field == "evidence":
        result = retain_case(module, root, stage)
        assert result["status"] == "REVIEW_RETAINED"
    else:
        with pytest.raises(ValueError, match="omissions"):
            retain_case(module, root, stage)


def test_duplicate_json_field_is_rejected_before_lock_or_store(tmp_path):
    module = load_module()
    root, stage, _, _ = make_case(tmp_path, module)
    stage.write_bytes(b'{"author":"kb-bot","author":"kb-bot"}')
    with pytest.raises(ValueError, match="duplicate"):
        retain_case(module, root, stage)
    assert not (root / ".kb-write.lock").exists()
    assert not (root / "_tools/reviews").exists()


def test_candidate_hash_and_context_arguments_are_bound_exactly(tmp_path):
    module = load_module()
    root, stage, record, text = make_case(tmp_path, module)
    result = retain_case(module, root, stage)
    path = root / result["path"]
    with pytest.raises(ValueError, match="candidate_sha256"):
        module.verify_review(
            root,
            path,
            candidate_path=record["candidate_path"],
            candidate_text=text + "changed",
            queue_path=record["queue_path"],
            primary_locator=record["primary_locator"],
        )
    with pytest.raises(ValueError, match="candidate_path"):
        module.verify_review(
            root,
            path,
            candidate_path="topics/example/sources/other.md",
            candidate_text=text,
            queue_path=record["queue_path"],
            primary_locator=record["primary_locator"],
        )


@pytest.mark.parametrize("scope", ["excerpt", "empty"])
def test_reproducible_snapshot_needs_supplied_extraction_evidence(tmp_path, scope):
    module = load_module()
    root, stage, record, _ = make_case(
        tmp_path, module, reproducible_snapshot=True,
        retention_limitations="Retained extraction excludes images.",
    )
    if scope == "excerpt":
        record["evidence"] = [add_evidence(module, root, scope="excerpt")]
    stage.write_bytes(module.encoded(record))
    with pytest.raises(ValueError, match="supplied-extraction|retained evidence"):
        retain_case(module, root, stage)


def test_evidence_tamper_and_provenance_are_detected(tmp_path):
    module = load_module()
    root, stage, record, text = make_case(tmp_path, module)
    item = add_evidence(module, root)
    record["evidence"] = [item]
    record["reproducible_snapshot"] = True
    record["retention_limitations"] = "Retained supplied extraction excludes images."
    stage.write_bytes(module.encoded(record))
    result = retain_case(module, root, stage)
    path = root / result["path"]
    evidence_path = root / item["path"]
    envelope = json.loads(evidence_path.read_text(encoding="utf-8"))
    envelope["limitations"] = "tampered"
    evidence_path.write_bytes(module._retain().encoded(envelope))
    with pytest.raises(ValueError):
        module.verify_canonical(root, path)

    # The review cannot smuggle in a retained companion source even when the
    # envelope itself is structurally valid.
    other = add_evidence(module, root, origin="https://companion.example/other")
    record["evidence"] = [other]
    stage.write_bytes(module.encoded(record))
    # Restore the first envelope's bytes so the canonical receipt remains the
    # only artifact under test; retain the new review under its new digest.
    evidence_path.write_bytes(module._retain().encoded({**json.loads(evidence_path.read_text()), "limitations": "Synthetic fixture only."}))
    with pytest.raises(ValueError):
        # This staged record has a different digest and is retained separately.
        new_result = retain_case(module, root, stage)
        verify_case(module, root, root / new_result["path"], record, text)


def test_unsafe_stage_inputs_are_rejected_without_reading_or_writing(tmp_path):
    module = load_module()
    for kind in ("symlink", "hardlink", "fifo"):
        case = tmp_path / kind
        root, stage, _, _ = make_case(case, module)
        outside = case / "outside.json"
        outside.write_bytes(stage.read_bytes())
        if kind == "symlink":
            stage.unlink()
            stage.symlink_to(outside)
        elif kind == "hardlink":
            stage.unlink()
            os.link(outside, stage)
        else:
            stage.unlink()
            os.mkfifo(stage)
        with pytest.raises(ValueError):
            retain_case(module, root, stage)
        assert not (root / ".kb-write.lock").exists()


def test_defer_and_reject_receipts_cannot_publish(tmp_path):
    module = load_module()
    for disposition in ("defer", "reject"):
        root, stage, record, text = make_case(tmp_path / disposition, module, disposition=disposition)
        result = retain_case(module, root, stage)
        with pytest.raises(ValueError, match="does not permit publication"):
            verify_case(module, root, root / result["path"], record, text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coverage", {"status": [], "covered": ["x"], "omissions": []}),
        ("disposition", []),
        ("reproducible_snapshot", "false"),
    ],
)
def test_wrong_json_types_raise_value_error(tmp_path, field, value):
    module = load_module()
    root, stage, _, _ = make_case(tmp_path, module, **{field: value})
    with pytest.raises(ValueError):
        retain_case(module, root, stage)
