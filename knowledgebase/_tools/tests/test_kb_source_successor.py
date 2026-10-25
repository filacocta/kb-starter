from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from review_fixture import make_review


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-source-successor.py"


def load_successor():
    spec = importlib.util.spec_from_file_location("kb_source_successor_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_text(
    title: str,
    origin: str,
    *,
    media: str | None = None,
    mirror: str | None = None,
) -> str:
    media_block = "media: []" if media is None else media
    mirror_line = [] if mirror is None else [f'mirror: "{mirror}"']
    return "\n".join(
        [
            "---",
            "author: kb-bot",
            "created: 2026-08-01",
            "modified: 2026-08-01",
            "type: source",
            "topic: example",
            f"title: {title}",
            "description: A test-only source record for validation.",
            "status: draft",
            "ingest_status: succeeded",
            f'origin: "{origin}"',
            *mirror_line,
            "tags: []",
            "sources: []",
            "related: []",
            media_block,
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


OLD_MEDIA = """media:
  - id: old-image
    kind: image
    status: discovered
    origin_url: https://example.test/old-image.png
    license: unknown"""


def make_case(
    tmp_path: Path,
    *,
    old_origin: str | None = None,
    same_origin: bool = False,
    replacement_origin: str | None = None,
    old_mirror: str | None = None,
    new_mirror: str | None = None,
):
    successor = load_successor()
    root = tmp_path / "knowledgebase"
    source_dir = root / "topics" / "example" / "sources"
    source_dir.mkdir(parents=True)
    (root / "_tools" / "reviews").mkdir(parents=True)

    old_origin = old_origin or "https://example.test/document"
    new_origin = (
        replacement_origin
        if replacement_origin is not None
        else old_origin if same_origin else "https://example.test/maintained-document"
    )
    old_text = source_text("Old source", old_origin, media=OLD_MEDIA, mirror=old_mirror)
    new_text = source_text("Maintained source", new_origin, mirror=new_mirror)
    old_path = source_dir / "old.md"
    old_path.write_text(old_text, encoding="utf-8")
    staged = tmp_path / "staged-new.md"
    staged.write_text(new_text, encoding="utf-8")

    queue = root / "topics" / "example" / "reading-list.md"
    queue.write_text(
        "\n".join(
            [
                "---",
                "author: kb-bot",
                "type: meta",
                "---",
                "## Approved",
                "",
                f"- [ ] {new_origin}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    new_rel = "topics/example/sources/new.md"
    queue_rel = "topics/example/reading-list.md"
    review_path = make_review(
        root,
        new_text,
        candidate=new_rel,
        queue=queue_rel,
        locator=new_origin,
    )
    raw_frontmatter, body = successor._frontmatter_parts(old_text, old_path)
    manifest = {
        "author": "kb-bot",
        "version": 1,
        "operation": "source-successor",
        "old": {
            "path": "topics/example/sources/old.md",
            "origin": old_origin,
            "sha256": hashlib.sha256(old_text.encode()).hexdigest(),
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "frontmatter_sha256": successor._immutable_frontmatter_sha(raw_frontmatter),
            "status": "draft",
        },
        "new": {
            "staged_path": str(staged),
            "path": new_rel,
            "origin": new_origin,
            "sha256": hashlib.sha256(new_text.encode()).hexdigest(),
        },
        "queue": {
            "path": queue_rel,
            "locator": new_origin,
            "section": "Approved",
        },
        "review": {
            "path": review_path.relative_to(root).as_posix(),
            "sha256": review_path.stem,
        },
        "reason": "The maintained document is now the approved replacement",
        "checkpoint": {"id": "successor-test-1", "state": "prepared"},
    }
    manifest_path = tmp_path / "successor.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return successor, root, old_path, staged, queue, manifest_path, manifest


def disable_regeneration(successor, monkeypatch):
    checkoff = successor._checkoff()
    monkeypatch.setattr(checkoff, "regenerate_dive_queue", lambda root, **kwargs: True)
    return checkoff


def test_successor_completes_and_preserves_old_body_modified_and_media(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    old_before = old.read_text(encoding="utf-8")
    _raw_before, old_body_before = successor._frontmatter_parts(old_before, old)
    old_fm_before, _raw_before, _body_before = successor._source_metadata(root, old, old_before)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "SUCCESSOR_COMPLETE"
    assert (root / manifest["new"]["path"]).read_text(encoding="utf-8") == staged.read_text()
    queue_text = queue.read_text(encoding="utf-8")
    assert "- [x] https://example.test/maintained-document" in queue_text
    assert "validated-source [[knowledgebase/topics/example/sources/new]] sha256:" in queue_text
    assert "review-receipt sha256:" in queue_text

    old_after = old.read_text(encoding="utf-8")
    _raw_after, old_body_after = successor._frontmatter_parts(old_after, old)
    old_fm_after, _raw_after, _body_after = successor._source_metadata(root, old, old_after)
    restored_body, date = successor._body_without_banner(
        old_body_after,
        manifest["reason"],
        successor._successor_link(manifest["new"]["path"]),
    )
    assert date
    assert restored_body == old_body_before
    assert old_fm_after["status"] == "deprecated"
    assert old_fm_after["superseded_by"] == successor._successor_link(manifest["new"]["path"])
    assert old_fm_after["modified"] == old_fm_before["modified"]
    assert old_fm_after["media"] == old_fm_before["media"]
    assert Path(result["manifest"]).is_absolute() is False
    assert (root / result["manifest"]).is_file()
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"


def test_ordinary_publisher_still_rejects_same_origin(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path, same_origin=True
    )
    disable_regeneration(successor, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "kb_commit_source_successor_test", ROOT / "_tools" / "kb-commit-source.py"
    )
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    review_path = root / manifest["review"]["path"]
    old_before = old.read_bytes()

    with pytest.raises(ValueError, match="origin already exists"):
        publisher.commit_source(
            root,
            staged,
            manifest["new"]["path"],
            queue,
            manifest["new"]["origin"],
            review_path=review_path,
        )
    assert not (root / manifest["new"]["path"]).exists()
    assert "- [ ] " in queue.read_text(encoding="utf-8")
    assert old.read_bytes() == old_before


def test_dedicated_successor_allows_only_manifested_origin_collision(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path, same_origin=True
    )
    disable_regeneration(successor, monkeypatch)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "SUCCESSOR_COMPLETE"
    queue_text = queue.read_text(encoding="utf-8")
    assert "warnings-accepted: successor " + result["manifest_sha256"] in queue_text


def test_same_origin_rule30_mirror_collision_is_allowed(tmp_path, monkeypatch):
    origin = "https://x.com/thedankoe/status/2069186019560394833"
    mirror = "https://api.fxtwitter.com/2/thread/2069186019560394833"
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path,
        old_origin=origin,
        same_origin=True,
        old_mirror=mirror,
        new_mirror=mirror,
    )
    disable_regeneration(successor, monkeypatch)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "SUCCESSOR_COMPLETE"
    assert (root / manifest["new"]["path"]).read_text(encoding="utf-8") == staged.read_text(
        encoding="utf-8"
    )
    assert "- [x] " + origin in queue.read_text(encoding="utf-8")
    assert "status: deprecated" in old.read_text(encoding="utf-8")


def test_same_origin_different_predecessor_mirror_endpoint_stops(tmp_path, monkeypatch):
    origin = "https://x.com/thedankoe/status/2069186019560394833"
    old_mirror = "https://api.fxtwitter.com/2/thread/2069186019560394834"
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path,
        old_origin=origin,
        same_origin=True,
        old_mirror=old_mirror,
        new_mirror=old_mirror,
    )
    disable_regeneration(successor, monkeypatch)
    del staged

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "identity is ambiguous" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "status: draft" in old.read_text(encoding="utf-8")
    assert "- [ ] " + origin in queue.read_text(encoding="utf-8")


def test_distinct_new_origin_cannot_reuse_old_origin_as_mirror(tmp_path, monkeypatch):
    old_origin = "https://x.com/thedankoe/status/2069186019560394833"
    old_mirror = "https://api.fxtwitter.com/2/thread/2069186019560394833"
    new_origin = "https://x.com/thedankoe/status/2069186019560394834"
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path,
        old_origin=old_origin,
        replacement_origin=new_origin,
        old_mirror=old_mirror,
        new_mirror=old_origin,
    )
    disable_regeneration(successor, monkeypatch)
    del staged

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "identity is ambiguous" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "status: draft" in old.read_text(encoding="utf-8")
    assert "- [ ] " + new_origin in queue.read_text(encoding="utf-8")


def test_origin_collision_warning_must_name_exact_predecessor_path(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    validator = successor._validator()
    fake_message = (
        "origin matches existing source note: "
        + manifest["old"]["path"]
        + "-copy.md"
    )
    monkeypatch.setattr(
        validator,
        "validate_text",
        lambda *args, **kwargs: [
            validator.Finding(
                "warn",
                "origin-collision",
                manifest["new"]["path"],
                fake_message,
            )
        ],
    )

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "unapproved validation warnings" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "- [ ] " in queue.read_text(encoding="utf-8")


def test_replacement_null_successor_metadata_is_not_treated_as_absent(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    candidate = staged.read_text(encoding="utf-8").replace(
        "status: draft\n", "status: draft\nsuperseded_by: null\n", 1
    )
    manifest["new"]["sha256"] = hashlib.sha256(candidate.encode()).hexdigest()

    with pytest.raises(successor.SuccessorError, match="successor metadata"):
        successor._validate_new_candidate(root, manifest, candidate)


def test_same_origin_extra_active_source_stops_before_old_deprecation(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path, same_origin=True
    )
    disable_regeneration(successor, monkeypatch)
    # Keep the extra source after the predecessor lexically so the validator's
    # first collision warning still names the predecessor; the full active-set
    # gate must nevertheless stop before publishing anything.
    extra = root / "topics/example/sources/z-extra.md"
    extra.write_text(
        staged.read_text(encoding="utf-8").replace(
            "Maintained source", "Extra active source", 1
        ),
        encoding="utf-8",
    )

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "- [ ] " in queue.read_text()
    assert not (root / manifest["new"]["path"]).exists()
    assert "status: draft" in old.read_text()


def test_successor_rejects_predecessor_mirror_as_new_origin(tmp_path, monkeypatch):
    predecessor_mirror = "https://example.test/maintained-document"
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path,
        replacement_origin=predecessor_mirror,
        old_mirror=predecessor_mirror,
    )
    disable_regeneration(successor, monkeypatch)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "identity is ambiguous" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "status: draft" in old.read_text(encoding="utf-8")
    assert "- [ ] " in queue.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("existing_field", "candidate_field"),
    [
        ("mirror", "origin"),
        ("origin", "mirror"),
        ("mirror", "mirror"),
    ],
)
def test_successor_rejects_third_party_origin_mirror_overlap(
    tmp_path, monkeypatch, existing_field, candidate_field
):
    third_party_origin = "https://example.test/third-party-origin"
    third_party_mirror = "https://example.test/third-party-mirror"
    candidate_mirror = None
    if candidate_field == "mirror":
        candidate_mirror = (
            third_party_origin if existing_field == "origin" else third_party_mirror
        )
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path, new_mirror=candidate_mirror
    )
    del staged
    third_party = root / "topics/example/sources/third-party.md"
    third_party.write_text(
        source_text(
            "Third-party source",
            third_party_origin,
            mirror=(
                manifest["new"]["origin"]
                if existing_field == "mirror" and candidate_field == "origin"
                else third_party_mirror if existing_field == "mirror" else None
            ),
        ),
        encoding="utf-8",
    )
    disable_regeneration(successor, monkeypatch)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "identity is ambiguous" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "- [ ] " in queue.read_text(encoding="utf-8")


def test_successor_rejects_replacement_mirror_governed_alias(tmp_path, monkeypatch):
    alias = "https://example.test/legacy-maintained-document"
    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path, new_mirror=alias
    )
    alias_target = root / "topics/example/sources/alias-target.md"
    alias_target.write_text(
        source_text("Alias target", "https://example.test/alias-target"),
        encoding="utf-8",
    )
    (root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": alias,
                        "source_note": "topics/example/sources/alias-target.md",
                        "evidence": "successor identity regression fixture",
                        "verified": "2026-09-10",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    disable_regeneration(successor, monkeypatch)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert "governed alias" in result["error"]
    assert not (root / manifest["new"]["path"]).exists()
    assert "- [ ] " in queue.read_text(encoding="utf-8")


def test_final_different_origin_predecessor_race_stops_before_deprecation(
    tmp_path, monkeypatch
):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    original = successor._assert_single_predecessor_set
    injected = root / "topics/example/sources/z-injected.md"

    def inject_extra_predecessor(root, manifest, **kwargs):
        injected.write_text(
            source_text("Injected predecessor", manifest["old"]["origin"]),
            encoding="utf-8",
        )
        return original(root, manifest, **kwargs)

    monkeypatch.setattr(successor, "_assert_single_predecessor_set", inject_extra_predecessor)

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert result["state"] == "lifecycle-preflight-failed"
    assert "predecessor identity is ambiguous" in result["error"]
    assert (root / manifest["new"]["path"]).is_file()
    assert "- [x] " in queue.read_text(encoding="utf-8")
    assert "status: draft" in old.read_text(encoding="utf-8")


def test_resume_rechecks_different_origin_predecessor_set_under_lock(
    tmp_path, monkeypatch
):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    guard = successor._guard()
    original_write = guard.atomic_write
    monkeypatch.setattr(
        guard,
        "atomic_write",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("pause before lifecycle")),
    )
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING"
    monkeypatch.setattr(guard, "atomic_write", original_write)

    manifest_data, _digest, _canonical = successor._parse_manifest(root, manifest_path)
    old.write_text(
        successor._deprecated_text(old.read_text(encoding="utf-8"), manifest_data),
        encoding="utf-8",
    )
    injected = root / "topics/example/sources/z-injected.md"
    injected.write_text(
        source_text("Injected predecessor", manifest["old"]["origin"]),
        encoding="utf-8",
    )

    resumed = successor.run_successor(root, manifest_path)

    assert resumed["status"] == "STOP"
    assert resumed["state"] == "lifecycle-preflight-failed"
    assert "predecessor identity is ambiguous" in resumed["error"]


def test_resume_after_source_write_crash(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    guard = successor._guard()
    original = guard.atomic_create

    def create_then_crash(path, text, *, prefix=".kb-create-"):
        original(path, text, prefix=prefix)
        if prefix == ".successor-source-":
            raise OSError("simulated crash after source install")

    monkeypatch.setattr(guard, "atomic_create", create_then_crash)
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING"
    assert (root / manifest["new"]["path"]).read_text() == staged.read_text()
    assert "- [ ] " in queue.read_text()
    assert "status: draft" in old.read_text()

    monkeypatch.setattr(guard, "atomic_create", original)
    second = successor.run_successor(root, manifest_path)
    assert second["status"] == "SUCCESSOR_COMPLETE"


def test_resume_after_queue_completion_failure(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    checkoff = disable_regeneration(successor, monkeypatch)
    original = checkoff.checkoff

    def queue_failure(*args, **kwargs):
        return False, "simulated queue failure"

    monkeypatch.setattr(checkoff, "checkoff", queue_failure)
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING"
    assert (root / manifest["new"]["path"]).is_file()
    assert "- [ ] " in queue.read_text()
    assert "status: draft" in old.read_text()

    monkeypatch.setattr(checkoff, "checkoff", original)
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"


def test_resume_after_old_lifecycle_failure(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    guard = successor._guard()
    original = guard.atomic_write

    def lifecycle_failure(*args, **kwargs):
        raise OSError("simulated lifecycle crash")

    monkeypatch.setattr(guard, "atomic_write", lifecycle_failure)
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING"
    assert "- [x] " in queue.read_text()
    assert "status: draft" in old.read_text()

    monkeypatch.setattr(guard, "atomic_write", original)
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"


def test_dashboard_regeneration_failure_is_explicit_and_resumable(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    checkoff = disable_regeneration(successor, monkeypatch)

    def regeneration_failure(root, *, strict=False):
        assert strict is True
        return False

    monkeypatch.setattr(checkoff, "regenerate_dive_queue", regeneration_failure)
    first = successor.run_successor(root, manifest_path)

    assert first["status"] == "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING"
    assert "dashboard regeneration failed" in first["error"]
    assert "- [x] " in queue.read_text(encoding="utf-8")
    assert "status: draft" in old.read_text(encoding="utf-8")

    monkeypatch.setattr(checkoff, "regenerate_dive_queue", lambda root, **kwargs: True)
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        ("SUCCESSOR_SOURCE_WRITTEN_QUEUE_PENDING", 1),
        ("SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING", 1),
        ("STOP", 2),
        ("SUCCESSOR_COMPLETE", 0),
    ],
)
def test_cli_distinguishes_partial_stop_and_complete_states(
    tmp_path, monkeypatch, capsys, status, expected_exit
):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    monkeypatch.setattr(
        successor,
        "run_successor",
        lambda root, manifest_path: {"status": status, "state": "fixture"},
    )

    assert successor.main(
        ["--kb-root", str(root), "--manifest", str(manifest_path)]
    ) == expected_exit
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_preflight_hash_mismatch_writes_nothing(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    staged.write_text(staged.read_text() + "\nchanged", encoding="utf-8")
    before = queue.read_bytes()

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert queue.read_bytes() == before
    assert not (root / manifest["new"]["path"]).exists()
    assert not (root / "_tools" / "successors").exists()


def test_old_body_or_media_hash_mismatch_stops_without_writes(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    old.write_text(old.read_text().replace("Old source", "Changed old source", 1), encoding="utf-8")
    before = queue.read_bytes()

    result = successor.run_successor(root, manifest_path)

    assert result["status"] == "STOP"
    assert queue.read_bytes() == before
    assert not (root / manifest["new"]["path"]).exists()


def test_staged_symlink_and_old_symlink_are_rejected(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text(staged.read_text(), encoding="utf-8")
    staged.unlink()
    staged.symlink_to(outside)
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()

    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path / "second"
    )
    disable_regeneration(successor, monkeypatch)
    old_copy = tmp_path / "second-old.md"
    old_copy.write_text(old.read_text(), encoding="utf-8")
    old.unlink()
    old.symlink_to(old_copy)
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()


def test_manifest_scope_receipt_and_queue_identity_are_strict(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    payload = json.loads(manifest_path.read_text())
    payload["unexpected"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()

    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path / "receipt"
    )
    disable_regeneration(successor, monkeypatch)
    payload = json.loads(manifest_path.read_text())
    payload["review"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()

    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path / "queue"
    )
    disable_regeneration(successor, monkeypatch)
    queue.write_text(queue.read_text().replace("- [ ]", "- [ ]", 1).replace(
        manifest["queue"]["locator"], "https://example.test/other", 1
    ))
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()


def test_human_owned_predecessor_and_target_symlink_are_rejected(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    old.write_text(old.read_text().replace("author: kb-bot", "author: human", 1))
    assert successor.run_successor(root, manifest_path)["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()

    successor, root, old, staged, queue, manifest_path, manifest = make_case(
        tmp_path / "target"
    )
    disable_regeneration(successor, monkeypatch)
    target = root / manifest["new"]["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "target-outside.md"
    outside.write_text(staged.read_text(), encoding="utf-8")
    target.symlink_to(outside)
    assert successor.run_successor(root, manifest_path)["status"] == "STOP"
    assert "- [ ] " in queue.read_text()


def test_completed_queue_requires_exact_source_and_review_receipts(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    queue.write_text(
        queue.read_text().replace("- [ ] ", "- [x] ") + "",
        encoding="utf-8",
    )
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert not (root / manifest["new"]["path"]).exists()


def test_canonical_manifest_is_immutable_and_missing_partial_fails_closed(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    original = successor._canonical_manifest

    def manifest_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated manifest checkpoint crash")

    monkeypatch.setattr(successor, "_canonical_manifest", manifest_crash)
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "STOP"
    successors = root / "_tools" / "successors"
    assert successors.is_dir() and len(list(successors.iterdir())) == 1
    assert not (root / manifest["new"]["path"]).exists()

    monkeypatch.setattr(successor, "_canonical_manifest", original)
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"

    canonical = next(successors.iterdir())
    canonical.write_text(canonical.read_text() + "tampered\n", encoding="utf-8")
    assert successor.run_successor(root, manifest_path)["status"] == "STOP"


def test_old_deprecated_state_with_media_mutation_cannot_resume(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    guard = successor._guard()
    original = guard.atomic_write
    guard.atomic_write = lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("pause before lifecycle")
    )
    first = successor.run_successor(root, manifest_path)
    assert first["status"] == "SUCCESSOR_SOURCE_AND_QUEUE_COMPLETE_OLD_PENDING"
    guard.atomic_write = original
    # Complete the transition, then mutate only the old media inventory.  The
    # exact manifest must not trust status/superseded_by alone on a later run.
    assert successor.run_successor(root, manifest_path)["status"] == "SUCCESSOR_COMPLETE"
    old.write_text(old.read_text().replace("old-image", "changed-image", 1))
    assert successor.run_successor(root, manifest_path)["status"] == "STOP"


def test_hardlinked_target_is_rejected_without_queue_write(tmp_path, monkeypatch):
    successor, root, old, staged, queue, manifest_path, manifest = make_case(tmp_path)
    disable_regeneration(successor, monkeypatch)
    target = root / manifest["new"]["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(staged, target)
    before = queue.read_bytes()
    result = successor.run_successor(root, manifest_path)
    assert result["status"] == "STOP"
    assert queue.read_bytes() == before
