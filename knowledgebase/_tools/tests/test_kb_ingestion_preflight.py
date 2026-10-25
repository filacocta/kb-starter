from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-ingestion-preflight.py"
FRONTMATTER = "---\nauthor: kb-bot\ntype: meta\n---\n"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_ingestion_preflight_tests", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path, body: str, *, topic: str = "demo") -> tuple[Path, Path]:
    root = tmp_path / "kb"
    queue = root / "topics" / topic / "reading-list.md"
    queue.parent.mkdir(parents=True)
    (root / "readinglist").mkdir(parents=True)
    queue.write_text(FRONTMATTER + body, encoding="utf-8")
    (root / "readinglist" / "Links.md").write_text(FRONTMATTER, encoding="utf-8")
    return root, queue


def write_source(root: Path, name: str, origin: str, *, topic: str = "demo") -> Path:
    source = root / "topics" / topic / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "---\n"
        "author: kb-bot\n"
        "type: source\n"
        f"origin: {origin}\n"
        "---\nsource body\n",
        encoding="utf-8",
    )
    return source


def test_exact_locator_and_prose_rows_ignore_annotations_and_fences(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        """## Pending
- [ ] https://example.com/pending

## Approved
- [ ] https://example.com/one — note mentions https://example.com/two
- [ ] prose row mentioning https://example.com/three later
```markdown
## Approved
- [ ] https://example.com/fenced
```

## Auto-approved
- [ ] https://example.com/four
""",
    )

    result = module.preflight(root)

    assert result["state"] == "ok"
    assert result["global_inbox_unchecked_count"] == 0
    assert [item["locator"] for item in result["items"]] == [
        "https://example.com/one",
        None,
        "https://example.com/four",
    ]
    assert all(item["channel_needs"] == "unknown" for item in result["items"])
    assert len(result["eligible_candidates"]) == 2
    assert result["items"][0]["raw_row"].endswith("https://example.com/two")


def test_tilde_and_mixed_length_fences_ignore_rows_until_matching_close(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        """## Approved
~~~markdown
- [ ] https://example.com/tilde-inside
```
- [ ] https://example.com/mixed-inside
~~~~
- [ ] https://example.com/tilde-after-longer-close
## Auto-approved
~~~~
- [ ] https://example.com/short-close-still-inside
~~~
- [ ] https://example.com/mixed-two-still-inside
~~~~~
- [ ] https://example.com/after-five-close
""",
    )

    result = module.preflight(root)

    assert [item["locator"] for item in result["items"]] == [
        "https://example.com/tilde-after-longer-close",
        "https://example.com/after-five-close",
    ]
    assert len(result["eligible_candidates"]) == 2


def test_uppercase_url_is_not_eligible_when_primary_locator_cannot_parse_it(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] HTTPS://EXAMPLE.COM/item\n")

    result = module.preflight(root)

    assert result["eligible_candidates"] == []
    assert result["items"][0]["locator"] is None
    assert result["items"][0]["reason"] == "unsupported-locator-case"


def test_human_owned_selected_queue_stops_without_candidates(tmp_path: Path) -> None:
    module = load_module()
    root, queue = make_kb(tmp_path, "## Approved\n- [ ] https://example.com/item\n")
    queue.write_text(
        "---\nauthor: human\ntype: meta\n---\n## Approved\n- [ ] https://example.com/item\n",
        encoding="utf-8",
    )

    result = module.preflight(root, "demo")

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []


def test_selected_queue_symlink_stops_without_candidates(tmp_path: Path) -> None:
    module = load_module()
    root, queue = make_kb(tmp_path, "## Approved\n- [ ] https://example.com/item\n")
    target = tmp_path / "external-queue.md"
    target.write_text(queue.read_text(encoding="utf-8"), encoding="utf-8")
    queue.unlink()
    queue.symlink_to(target)

    result = module.preflight(root, "demo")

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []


def test_symlink_topic_directory_stops_default_scan_without_candidates(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n")
    external = tmp_path / "external-topic"
    external.mkdir()
    (external / "reading-list.md").write_text(
        FRONTMATTER + "## Approved\n- [ ] https://example.com/item\n", encoding="utf-8"
    )
    (root / "topics" / "linked").symlink_to(external, target_is_directory=True)

    result = module.preflight(root)

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []


def test_missing_topics_root_stops_default_scan(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    root.mkdir()

    result = module.preflight(root)

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []


def test_missing_requested_topic_or_queue_stops(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "kb"
    (root / "topics" / "demo").mkdir(parents=True)
    (root / "readinglist").mkdir()
    (root / "readinglist" / "Links.md").write_text(FRONTMATTER, encoding="utf-8")

    missing_topic = module.preflight(root, "missing")
    missing_queue = module.preflight(root, "demo")

    assert missing_topic["state"] == "STOP"
    assert missing_queue["state"] == "STOP"
    assert missing_topic["eligible_candidates"] == []
    assert missing_queue["eligible_candidates"] == []


def test_noncanonical_topic_directory_slug_stops_default_scan(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n")
    (root / "topics" / "Not-Canonical").mkdir()

    result = module.preflight(root)

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []
    assert "noncanonical slug" in result["error"]


def test_casefold_matching_noncanonical_section_heading_stops(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## approved\n- [ ] https://example.com/item\n")

    result = module.preflight(root, "demo")

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []
    assert "noncanonical section heading" in result["error"]


def test_malformed_credentials_whitespace_and_nonstandard_port_are_ineligible(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        """## Approved
- [ ] https://
- [ ] https://user:pass@example.com/private
- [ ] https://example.com:8080/private
- [ ] https://example.com:bad/private
""",
    )

    result = module.preflight(root)

    assert result["state"] == "ok"
    assert result["eligible_candidates"] == []
    assert result["counts"]["invalid_urls"] == 4
    reasons = {item["reason"] for item in result["items"]}
    assert "unsupported-identity-port" in reasons
    assert "invalid-url-credentials" in reasons


def test_historical_nondefault_source_port_stops_preflight_without_unseen_fallback(
    tmp_path: Path,
) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://example.com/article\n")
    write_source(root, "legacy.md", "https://example.com:8443/article")

    result = module.preflight(root)

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []
    assert "source identity/index error" in result["error"]
    assert "nonstandard ports" in result["error"]


def test_local_pdf_is_metadata_only_and_never_sent_to_dedup(tmp_path: Path, monkeypatch) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Auto-approved\n- [ ] /tmp/not-present.pdf\n")
    present = tmp_path / "source.pdf"
    present.write_bytes(b"synthetic PDF payload that must not be read")
    missing = "/tmp/not-present.pdf"
    queue = root / "topics" / "demo" / "reading-list.md"
    queue.write_text(
        FRONTMATTER + f"## Auto-approved\n- [ ] {present}\n- [ ] {missing}\n",
        encoding="utf-8",
    )
    calls: list[int] = []
    original_open = module.WRITE_GUARD._open_regular

    def checked_open(path, *, flags, **kwargs):
        calls.append(flags)
        return original_open(path, flags=flags, **kwargs)

    monkeypatch.setattr(module.WRITE_GUARD, "_open_regular", checked_open)
    monkeypatch.setattr(
        module.SEEN,
        "source_for_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local PDF reached URL dedup")),
    )

    result = module.preflight(root)

    assert result["eligible_candidates"] == []
    assert [item["local_file"] for item in result["items"]] == ["present", "missing"]
    assert all(item["dedup"]["status"] == "unknown" for item in result["items"])
    assert all(flag & getattr(os, "O_NONBLOCK", 0) for flag in calls)


def test_normalized_active_queue_duplicates_are_queue_ambiguous(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        """## Approved
- [ ] https://example.com/same

## Auto-approved
- [ ] https://example.com/same?utm_source=fixture
""",
    )

    result = module.preflight(root)

    assert result["counts"]["queue_ambiguous"] == 2
    assert result["counts"]["eligible_candidates"] == 0
    assert all(item["reason"] == "queue-ambiguous" for item in result["items"])


def test_duplicate_source_identity_is_dedup_ambiguous_not_first_match(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://example.com/collision\n")
    write_source(root, "one.md", "https://example.com/collision")
    write_source(root, "two.md", "https://example.com/collision")

    result = module.preflight(root)

    assert result["state"] == "ok"
    assert result["eligible_candidates"] == []
    item = result["items"][0]
    assert item["dedup"]["status"] == "ambiguous"
    assert item["dedup"]["source_notes"] == [
        "topics/demo/sources/one.md",
        "topics/demo/sources/two.md",
    ]


def test_alias_mapping_is_validated_and_used_but_malformed_alias_stops(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://legacy.example/item\n")
    write_source(root, "current.md", "https://current.example/item")
    (root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": "https://legacy.example/item",
                        "source_note": "topics/demo/sources/current.md",
                        "evidence": "synthetic fixture",
                        "verified": "2026-09-08",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = module.preflight(root)

    assert result["items"][0]["dedup"]["status"] == "seen"
    assert result["items"][0]["dedup"]["source_note"] == "topics/demo/sources/current.md"
    assert result["alias_mappings"][0]["evidence"] == "synthetic fixture"

    (root / "_url-aliases.json").write_text("{not json", encoding="utf-8")
    stopped = module.preflight(root)
    assert stopped["state"] == "STOP"
    assert stopped["eligible_candidates"] == []


def test_stale_seen_cache_is_ignored(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://cache.example/not-ingested\n")
    (root / "_seen-urls.json").write_text(
        json.dumps({"https://cache.example/not-ingested": "topics/demo/sources/ghost.md"}),
        encoding="utf-8",
    )

    result = module.preflight(root)

    assert result["items"][0]["dedup"]["status"] == "unseen"
    assert len(result["eligible_candidates"]) == 1


def test_queue_change_between_snapshots_returns_snapshot_changed(tmp_path: Path, monkeypatch) -> None:
    module = load_module()
    root, queue = make_kb(tmp_path, "## Approved\n- [ ] https://example.com/before\n")
    original = module.WRITE_GUARD.guarded_text
    calls = 0

    def mutate_after_first(path, kb_root):
        nonlocal calls
        text = original(path, kb_root)
        calls += 1
        if calls == 1:
            queue.write_text(FRONTMATTER + "## Approved\n- [ ] https://example.com/after\n", encoding="utf-8")
        return text

    monkeypatch.setattr(module.WRITE_GUARD, "guarded_text", mutate_after_first)

    result = module.preflight(root)

    assert result["state"] == "snapshot_changed"
    assert result["eligible_candidates"] == []


def test_source_change_between_snapshots_returns_snapshot_changed(tmp_path: Path, monkeypatch) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://new.example/item\n")
    source = write_source(root, "current.md", "https://old.example/item")
    original = module._source_snapshot
    calls = 0

    def mutate_after_snapshot(path):
        nonlocal calls
        result = original(path)
        calls += 1
        if calls == 1:
            source.write_text(source.read_text(encoding="utf-8").replace("old.example", "changed.example"), encoding="utf-8")
        return result

    monkeypatch.setattr(module, "_source_snapshot", mutate_after_snapshot)

    result = module.preflight(root)

    assert result["state"] == "snapshot_changed"
    assert result["eligible_candidates"] == []


def test_global_inbox_is_counted_but_never_an_approved_candidate(tmp_path: Path) -> None:
    module = load_module()
    root, queue = make_kb(tmp_path, "## Pending\n")
    (root / "readinglist" / "Links.md").write_text(
        FRONTMATTER + "## Examples\n- [ ] https://global.example/item\n", encoding="utf-8"
    )

    result = module.preflight(root)

    assert result["global_inbox_unchecked_count"] == 1
    assert result["items"] == []
    assert result["eligible_candidates"] == []
    assert queue.exists()


def test_queue_file_sha256_covers_frontmatter_metadata(tmp_path: Path) -> None:
    module = load_module()
    root, queue = make_kb(tmp_path, "## Approved\n- [ ] https://hash.example/item\n")

    first = module.preflight(root)
    first_hash = first["items"][0]["file_sha256"]
    queue.write_text(queue.read_text(encoding="utf-8").replace("type: meta", "type: meta\ntitle: changed"), encoding="utf-8")
    second = module.preflight(root)

    assert len(first_hash) == 64
    assert second["items"][0]["file_sha256"] != first_hash


def test_busy_shared_lock_stops_without_partial_eligibility(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(tmp_path, "## Approved\n- [ ] https://busy.example/item\n")

    with module.WRITE_GUARD.write_lock(root):
        result = module.preflight(root)

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []
    assert result["queue_writes"] == []
    assert result["note_writes"] == []
    assert result["fetch_executed"] is False


def test_symlink_cycle_root_error_is_stop(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "cycle"
    root.mkdir()
    (root / "loop").symlink_to(root / "loop")

    result = module.preflight(root / "loop")

    assert result["state"] == "STOP"
    assert result["eligible_candidates"] == []


def test_fenced_approval_inside_pending_does_not_create_candidate(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        "## Pending\n"
        "```markdown\n"
        "## Approved\n"
        "- [ ] https://example.com/fake-approved\n"
        "```\n"
        "## Approved\n"
        "- [ ] https://example.com/actual-approved\n",
    )

    result = module.preflight(root)

    assert [item["locator"] for item in result["items"]] == [
        "https://example.com/actual-approved"
    ]
    assert result["items"][0]["section"] == "Approved"
    assert len(result["eligible_candidates"]) == 1


def test_fenced_duplicate_locator_is_not_queue_ambiguous(tmp_path: Path) -> None:
    module = load_module()
    root, _ = make_kb(
        tmp_path,
        "## Approved\n"
        "```\n"
        "- [ ] https://example.com/duplicate\n"
        "```\n"
        "- [ ] https://example.com/duplicate\n",
    )

    result = module.preflight(root)

    assert len(result["items"]) == 1
    assert result["counts"]["queue_ambiguous"] == 0
    assert len(result["eligible_candidates"]) == 1


def test_duplicate_or_malformed_approval_headings_fail_closed(tmp_path: Path) -> None:
    module = load_module()
    root, queue = make_kb(
        tmp_path,
        "## Approved\n- [ ] https://example.com/one\n"
        "## Approved\n- [ ] https://example.com/two\n",
    )

    duplicate = module.preflight(root)

    assert duplicate["state"] == "ok"
    assert duplicate["counts"]["queue_ambiguous"] == 2
    assert duplicate["eligible_candidates"] == []

    queue.write_text(
        FRONTMATTER + "## approved\n- [ ] https://example.com/malformed\n",
        encoding="utf-8",
    )
    malformed = module.preflight(root)

    assert malformed["state"] == "STOP"
    assert malformed["eligible_candidates"] == []
