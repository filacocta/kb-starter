from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import pytest
from pathlib import Path

from review_fixture import make_review


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-seen.py"


@pytest.mark.parametrize('url', [
    'https://user:pass@example.org/alias', 'https://user@example.org/alias',
    'https://@example.org/alias', 'https://example.org/a b',
    'https://example.org/a\tb', 'https://example.org:8443/alias',
    'https://example.org:bad/alias', 'https://example.org:/alias',
])
def test_alias_credentials_and_lossy_locators_fail_closed(tmp_path, url):
    seen = load_seen_module()
    write_source(tmp_path, 'source.md', 'https://example.org/source')
    write_registry(tmp_path, [dict(alias=url, source_note='topics/example/sources/source.md',
                                 evidence='test only', verified='2026-09-06')])
    with pytest.raises(seen.AliasRegistryError):
        seen.load_index(tmp_path)


def load_seen_module():
    spec = importlib.util.spec_from_file_location("kb_seen", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_source(
    kb_root: Path,
    name: str,
    origin: str,
    *,
    mirror: str | None = None,
    author: str = "kb-bot",
    ingest_status: str = "succeeded",
) -> Path:
    path = kb_root / "topics" / "example" / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    mirror_line = "" if mirror is None else f'mirror: "{mirror}"\n'
    path.write_text(
        "---\n"
        f"author: {author}\n"
        "type: source\n"
        "topic: example\n"
        f"origin: \"{origin}\"\n"
        + mirror_line
        + f"ingest_status: {ingest_status}\n"
        "---\n\n"
        "# Source\n",
        encoding="utf-8",
    )
    return path


def write_registry(kb_root: Path, entries) -> Path:
    path = kb_root / "_url-aliases.json"
    path.write_text(
        json.dumps({"author": "kb-bot", "version": 1, "entries": entries}),
        encoding="utf-8",
    )
    return path


def test_stale_cache_does_not_hide_new_x_mirror_source(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(kb_root, "older.md", "https://example.com/older")
    seen.build_index(kb_root)

    source = write_source(
        kb_root,
        "newer.md",
        "https://x.com/theo/status/2082277789395501263",
    )

    index = seen.load_index(kb_root)
    assert index["https://x.com/i/status/2082277789395501263"] == source.relative_to(kb_root).as_posix()
    assert seen.is_ingested(
        "https://fxtwitter.com/i/status/2082277789395501263",
        kb_root,
        index,
    ) == source.relative_to(kb_root).as_posix()


def test_legacy_mirror_lookup_remains_seen_without_successor_manifest(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(
        kb_root,
        "source.md",
        "https://example.com/canonical",
        mirror="https://mirror.example.com/legacy",
    )

    assert seen.source_for_url("https://mirror.example.com/legacy", kb_root) == (
        source.relative_to(kb_root).as_posix()
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:8443/article",
        "http://example.com:8080/article",
        "https://example.com:bad/article",
        "https://example.com:/article",
    ],
)
def test_identity_normalization_rejects_nondefault_and_malformed_ports(
    tmp_path: Path, url: str
) -> None:
    seen = load_seen_module()

    with pytest.raises(seen.AliasRegistryError, match="port"):
        seen.normalize(url)

    # The direct lookup boundary rejects before it can consult a supplied
    # snapshot, so a lossy caller cannot turn this into an unseen result.
    with pytest.raises(seen.AliasRegistryError, match="port"):
        seen.source_for_url(url, tmp_path, {})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com:443/article", "https://example.com/article"),
        ("http://example.com:80/article", "https://example.com/article"),
    ],
)
def test_default_ports_remain_normalization_compatible(
    url: str, expected: str
) -> None:
    seen = load_seen_module()

    assert seen.normalize(url) == expected


def test_ipv6_bare_host_is_validated_before_trailing_bracket_cleanup() -> None:
    seen = load_seen_module()

    # The legacy normalizer's punctuation cleanup still determines the
    # resulting key, but the valid closing bracket must not be treated as an
    # invalid port while enforcing the new identity guard.
    assert seen.validate_identity_url("https://[::1]") == "https://[::1]"


def test_historical_nondefault_port_source_fails_closed_instead_of_polluting_default(
    tmp_path: Path,
) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(kb_root, "legacy.md", "https://example.com:8443/article")

    with pytest.raises(seen.AliasRegistryError, match="nonstandard ports"):
        seen.source_for_url("https://example.com/article", kb_root)


def test_read_only_load_does_not_create_missing_cache(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "source.md", "https://example.com/source")

    assert seen.load_index(kb_root) == {"https://example.com/source": source.relative_to(kb_root).as_posix()}
    assert not (kb_root / "_seen-urls.json").exists()


def test_governed_alias_resolves_to_existing_successful_source(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "current.md", "https://example.com/current")
    write_registry(
        kb_root,
        [
            {
                "alias": "https://example.com/legacy",
                "source_note": source.relative_to(kb_root).as_posix(),
                "evidence": "manual redirect audit",
                "verified": "2026-09-06",
            }
        ],
    )

    assert seen.source_for_url("https://example.com/legacy", kb_root) == source.relative_to(kb_root).as_posix()
    assert seen.locators_match("https://example.com/legacy", "https://example.com/current", kb_root)


def test_alias_registry_is_strict_and_fails_closed(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "current.md", "https://example.com/current")
    write_registry(
        kb_root,
        [
            {
                "alias": "https://example.com/legacy",
                "source_note": source.relative_to(kb_root).as_posix(),
                "evidence": "manual redirect audit",
                "verified": "2026-09-06",
            }
        ],
    )
    registry = json.loads((kb_root / "_url-aliases.json").read_text())
    registry["author"] = "human"
    (kb_root / "_url-aliases.json").write_text(json.dumps(registry), encoding="utf-8")

    try:
        seen.load_alias_registry(kb_root)
    except seen.AliasRegistryError as exc:
        assert "author" in str(exc)
    else:
        raise AssertionError("invalid alias registry must fail closed")


def test_alias_collision_with_existing_origin_is_rejected(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "current.md", "https://example.com/current")
    write_registry(
        kb_root,
        [
            {
                "alias": "https://example.com/current",
                "source_note": source.relative_to(kb_root).as_posix(),
                "evidence": "incorrect duplicate",
                "verified": "2026-09-06",
            }
        ],
    )

    try:
        seen.scan_index(kb_root)
    except seen.AliasRegistryError as exc:
        assert "collides" in str(exc)
    else:
        raise AssertionError("alias/origin collision must fail closed")


def test_distinct_revision_urls_remain_distinct(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    first = write_source(kb_root, "first.md", "https://example.com/article?revision=1")
    second = write_source(kb_root, "second.md", "https://example.com/article?revision=2")

    index = seen.load_index(kb_root)
    assert index["https://example.com/article?revision=1"] == first.relative_to(kb_root).as_posix()
    assert index["https://example.com/article?revision=2"] == second.relative_to(kb_root).as_posix()
    assert seen.source_for_url("https://example.com/article?revision=1", kb_root) != seen.source_for_url(
        "https://example.com/article?revision=2", kb_root
    )


def test_read_only_scan_does_not_trust_cache_after_source_deletion(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "source.md", "https://example.com/source")
    seen.build_index(kb_root)
    source.unlink()

    assert seen.source_for_url("https://example.com/source", kb_root) is None


def test_invalid_ingest_status_fails_closed(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(
        kb_root,
        "invalid-status.md",
        "https://example.com/invalid-status",
        ingest_status="pending",
    )

    with pytest.raises(seen.AliasRegistryError, match="ingest_status"):
        seen.load_index(kb_root)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_build_rejects_unsafe_cache_without_external_overwrite(tmp_path: Path, kind: str) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(kb_root, "source.md", "https://example.com/source")
    outside = tmp_path / "outside-cache.json"
    outside.write_text("keep", encoding="utf-8")
    cache = kb_root / seen.SEEN_FILE
    if kind == "symlink":
        cache.symlink_to(outside)
    else:
        os.link(outside, cache)

    with pytest.raises(seen.AliasRegistryError, match="seen-index cache"):
        seen.build_index(kb_root)
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_source_note_fails_closed(tmp_path: Path, kind: str) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "unsafe.md", "https://example.com/unsafe")
    outside = tmp_path / "outside.md"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    if kind == "symlink":
        source.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, source)
    else:
        os.mkfifo(source)

    with pytest.raises(seen.AliasRegistryError, match="unsafe source index"):
        seen.load_index(kb_root)


def test_unowned_source_note_fails_closed_before_suppressing(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(kb_root, "human.md", "https://example.com/human", author="human")

    with pytest.raises(seen.AliasRegistryError, match="author: kb-bot"):
        seen.load_index(kb_root)


def test_failed_source_note_remains_excluded(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(
        kb_root,
        "failed.md",
        "https://example.com/failed",
        ingest_status="failed",
    )

    assert seen.load_index(kb_root) == {}


def test_unrelated_malformed_description_does_not_hide_source_identity(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "legacy.md", "https://example.com/legacy")
    source.write_text(
        "---\n"
        "author: kb-bot\n"
        "type: source\n"
        "description: Legacy text with an unquoted colon: still prose\n"
        "origin: https://example.com/legacy\n"
        "ingest_status: succeeded\n"
        "---\n\n"
        "# Legacy source\n",
        encoding="utf-8",
    )

    assert seen.source_for_url("https://example.com/legacy", kb_root) == source.relative_to(
        kb_root
    ).as_posix()


@pytest.mark.parametrize(
    ("identity_block", "error_fragment"),
    [
        ("author: kb-bot\nauthor: kb-bot", "duplicated"),
        ("metadata:\n  author: kb-bot", "top-level scalar"),
        ("author: [kb-bot]", "top-level scalar"),
        ("author: kb-bot\n  accidental continuation", "continuation"),
        ("Author: kb-bot", "non-canonical"),
        ("'author\": kb-bot", "non-canonical"),
    ],
)
def test_ambiguous_source_identity_fails_closed(
    tmp_path: Path, identity_block: str, error_fragment: str
) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "ambiguous.md", "https://example.com/ambiguous")
    source.write_text(
        "---\n"
        f"{identity_block}\n"
        "type: source\n"
        "origin: https://example.com/ambiguous\n"
        "ingest_status: succeeded\n"
        "---\n\n",
        encoding="utf-8",
    )

    with pytest.raises(seen.AliasRegistryError, match=error_fragment):
        seen.load_index(kb_root)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_alias_target_fails_closed(tmp_path: Path, kind: str) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    source = write_source(kb_root, "target.md", "https://example.com/target")
    outside = tmp_path / "outside.md"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    if kind == "symlink":
        source.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, source)
    else:
        os.mkfifo(source)

    with pytest.raises(seen.AliasRegistryError, match="alias source"):
        seen._successful_source_metadata(
            kb_root, "topics/example/sources/target.md"
        )


def _deprecated_source(path: Path, target: str) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "ingest_status: succeeded\n---",
        "ingest_status: succeeded\n"
        "status: deprecated\n"
        f"superseded_by: \"[[knowledgebase/{target[:-3]}]]\"\n"
        "---",
        1,
    )
    path.write_text(text, encoding="utf-8")


def _write_completed_successor(
    kb_root: Path,
    old: Path,
    new: Path,
    queue: Path,
    old_origin: str,
    new_origin: str,
) -> Path:
    """Create a canonical completed fixture through the successor contract."""
    successor_spec = importlib.util.spec_from_file_location(
        "kb_seen_successor_fixture", ROOT / "_tools" / "kb-source-successor.py"
    )
    assert successor_spec is not None and successor_spec.loader is not None
    successor = importlib.util.module_from_spec(successor_spec)
    successor_spec.loader.exec_module(successor)

    old_pre = old.read_text(encoding="utf-8").replace(
        "ingest_status: succeeded\n---",
        "ingest_status: succeeded\nstatus: draft\n---",
        1,
    )
    old.write_text(old_pre, encoding="utf-8")
    new_text = new.read_text(encoding="utf-8").replace(
        "ingest_status: succeeded\n---",
        "ingest_status: succeeded\nstatus: draft\n---",
        1,
    )
    new.write_text(new_text, encoding="utf-8")
    staged = kb_root.parent / "successor-staged.md"
    staged.write_text(new_text, encoding="utf-8")
    old_rel = old.relative_to(kb_root).as_posix()
    new_rel = new.relative_to(kb_root).as_posix()
    queue_rel = queue.relative_to(kb_root).as_posix()
    review = make_review(
        kb_root,
        new_text,
        candidate=new_rel,
        queue=queue_rel,
        locator=new_origin,
    )
    raw_fm, old_body = successor._frontmatter_parts(old_pre, old)
    payload = {
        "author": "kb-bot",
        "version": 1,
        "operation": "source-successor",
        "old": {
            "path": old_rel,
            "origin": old_origin,
            "sha256": hashlib.sha256(old_pre.encode()).hexdigest(),
            "body_sha256": hashlib.sha256(old_body.encode()).hexdigest(),
            "frontmatter_sha256": successor._immutable_frontmatter_sha(raw_fm),
            "status": "draft",
        },
        "new": {
            "staged_path": str(staged),
            "path": new_rel,
            "origin": new_origin,
            "sha256": hashlib.sha256(new_text.encode()).hexdigest(),
        },
        "queue": {"path": queue_rel, "locator": new_origin, "section": "Approved"},
        "review": {
            "path": review.relative_to(kb_root).as_posix(),
            "sha256": review.stem,
        },
        "reason": "The maintained source supersedes the historical capture",
        "checkpoint": {"id": "seen-successor-fixture", "state": "prepared"},
    }
    manifest_input = kb_root.parent / "successor-manifest-input.json"
    manifest_input.write_text(json.dumps(payload), encoding="utf-8")
    manifest, digest, canonical = successor._parse_manifest(kb_root, manifest_input)
    successor._ensure_store(kb_root)
    canonical_path = kb_root / "_tools" / "successors" / f"{digest}.json"
    canonical_path.write_text(canonical, encoding="utf-8")
    old.write_text(successor._deprecated_text(old_pre, manifest), encoding="utf-8")
    source_ref = (
        f"validated-source [[knowledgebase/{new_rel[:-3]}]] "
        f"sha256:{manifest['new']['sha256']}"
    )
    review_ref = f"review-receipt sha256:{manifest['review']['sha256']}"
    queue.write_text(
        queue.read_text(encoding="utf-8").replace(
            f"- [ ] {new_origin}",
            f"- [x] {new_origin} — successor fixture — {source_ref} — {review_ref}",
            1,
        ),
        encoding="utf-8",
    )
    return canonical_path


def test_legacy_deprecated_link_is_not_trusted_as_successor_authority(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    origin = "https://example.com/document"
    old = write_source(kb_root, "2026-01-old.md", origin)
    write_source(kb_root, "2026-09-new.md", origin)
    _deprecated_source(old, "topics/example/sources/2026-09-new.md")

    index = seen.load_index(kb_root)
    assert index[seen.normalize(origin)] == "topics/example/sources/2026-01-old.md"
    assert seen.source_for_url(origin, kb_root, index) == "topics/example/sources/2026-01-old.md"
    assert seen.is_ingested(origin, kb_root, index) == "topics/example/sources/2026-01-old.md"


def test_completed_manifest_resolves_one_same_origin_successor(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    origin = "https://example.com/document"
    old = write_source(kb_root, "2026-01-old.md", origin)
    new = write_source(kb_root, "2026-09-new.md", origin)
    queue = kb_root / "topics" / "example" / "reading-list.md"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n"
        f"- [ ] {origin}\n",
        encoding="utf-8",
    )
    _write_completed_successor(kb_root, old, new, queue, origin, origin)

    index = seen.load_index(kb_root)
    replacement = "topics/example/sources/2026-09-new.md"
    assert index[seen.normalize(origin)] == replacement
    assert seen.source_for_url(origin, kb_root, index) == replacement
    assert seen.is_ingested(origin, kb_root, index) == replacement


def test_completed_manifest_resolves_different_old_url_to_active_target(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    old_origin = "https://example.com/historical-document"
    new_origin = "https://maintained.example.com/current-document"
    old = write_source(kb_root, "2026-01-old.md", old_origin)
    new = write_source(kb_root, "2026-09-new.md", new_origin)
    queue = kb_root / "topics" / "example" / "reading-list.md"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n"
        f"- [ ] {new_origin}\n",
        encoding="utf-8",
    )
    _write_completed_successor(kb_root, old, new, queue, old_origin, new_origin)

    replacement = "topics/example/sources/2026-09-new.md"
    index = seen.load_index(kb_root)
    assert index[seen.normalize(old_origin)] == replacement
    assert seen.source_for_url(old_origin, kb_root) == replacement
    assert seen.source_for_url(new_origin, kb_root) == replacement


@pytest.mark.parametrize(
    ("old_mirror", "new_mirror", "third_origin", "third_mirror"),
    [
        (
            "https://example.com/current-document",
            None,
            None,
            None,
        ),
        (
            None,
            None,
            "https://example.com/third-party-origin",
            "https://example.com/current-document",
        ),
        (
            None,
            "https://example.com/third-party-origin",
            "https://example.com/third-party-origin",
            None,
        ),
        (
            None,
            "https://example.com/third-party-mirror",
            "https://example.com/third-party-origin",
            "https://example.com/third-party-mirror",
        ),
    ],
)
def test_completed_manifest_rejects_origin_mirror_identity_overlap(
    tmp_path: Path,
    old_mirror: str | None,
    new_mirror: str | None,
    third_origin: str | None,
    third_mirror: str | None,
) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    old_origin = "https://example.com/historical-document"
    new_origin = "https://example.com/current-document"
    old = write_source(kb_root, "2026-01-old.md", old_origin, mirror=old_mirror)
    new = write_source(kb_root, "2026-09-new.md", new_origin, mirror=new_mirror)
    if third_origin is not None:
        write_source(
            kb_root,
            "2026-02-third-party.md",
            third_origin,
            mirror=third_mirror,
        )
    queue = kb_root / "topics" / "example" / "reading-list.md"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n"
        f"- [ ] {new_origin}\n",
        encoding="utf-8",
    )
    _write_completed_successor(kb_root, old, new, queue, old_origin, new_origin)

    with pytest.raises(seen.AliasRegistryError, match="identity is ambiguous"):
        seen.load_index(kb_root)


def test_deprecated_source_without_successor_remains_seen(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    origin = "https://example.com/historical"
    old = write_source(kb_root, "2026-01-old.md", origin)
    _deprecated_source(old, "topics/example/sources/missing.md")

    assert seen.source_for_url(origin, kb_root) == "topics/example/sources/2026-01-old.md"


def test_broken_successor_store_symlink_fails_closed(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    write_source(kb_root, "source.md", "https://example.com/source")
    tools = kb_root / "_tools"
    tools.mkdir()
    (tools / "successors").symlink_to(tmp_path / "missing-successors", target_is_directory=True)

    with pytest.raises(seen.AliasRegistryError, match="manifest store"):
        seen.load_index(kb_root)


def test_completed_manifest_stops_on_multiple_active_targets(tmp_path: Path) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    origin = "https://example.com/ambiguous-successor"
    old = write_source(kb_root, "2026-01-old.md", origin)
    new = write_source(kb_root, "2026-09-new.md", origin)
    queue = kb_root / "topics" / "example" / "reading-list.md"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "---\nauthor: kb-bot\ntype: meta\n---\n## Approved\n\n"
        f"- [ ] {origin}\n",
        encoding="utf-8",
    )
    _write_completed_successor(kb_root, old, new, queue, origin, origin)
    write_source(kb_root, "2026-10-other.md", origin)

    with pytest.raises(seen.AliasRegistryError, match="ambiguous successor"):
        seen.source_for_url(origin, kb_root)


@pytest.mark.parametrize(
    ("target_value", "extra_target"),
    [
        ("https://example.com/not-a-wikilink", None),
        (
            "topics/example/sources/2026-09-new.md",
            "superseded_by: \"[[knowledgebase/topics/example/sources/2026-10-next]]\"",
        ),
    ],
)
def test_unmanifested_or_incomplete_lineage_is_not_projected(
    tmp_path: Path, target_value: str, extra_target: str | None
) -> None:
    seen = load_seen_module()
    kb_root = tmp_path / "kb"
    origin = "https://example.com/lineage"
    old = write_source(kb_root, "2026-01-old.md", origin)
    new = write_source(kb_root, "2026-09-new.md", origin)
    if target_value.startswith("http"):
        text = old.read_text(encoding="utf-8").replace(
            "ingest_status: succeeded\n---",
            "ingest_status: succeeded\nstatus: deprecated\n"
            f"superseded_by: \"{target_value}\"\n---",
            1,
        )
        old.write_text(text, encoding="utf-8")
    else:
        _deprecated_source(old, target_value)
        assert extra_target is not None
        new.write_text(
            new.read_text(encoding="utf-8").replace(
                "ingest_status: succeeded\n---",
                "ingest_status: succeeded\n" + extra_target + "\n---",
                1,
            ),
            encoding="utf-8",
        )

    # Lifecycle-looking fields are historical metadata unless a canonical,
    # post-state-verified successor manifest binds them.  An incomplete or
    # malformed transition therefore keeps the old URL positively seen but
    # does not redirect it to an arbitrary target.
    assert seen.source_for_url(origin, kb_root) == "topics/example/sources/2026-01-old.md"
