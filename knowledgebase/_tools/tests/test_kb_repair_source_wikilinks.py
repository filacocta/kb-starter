from __future__ import annotations

import importlib.util
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-repair-source-wikilinks.py"

SOURCE_FRONTMATTER = """---
author: kb-bot
created: 2026-09-07
modified: 2026-09-07
type: source
topic: example
title: Example Source
description: An example source note.
status: draft
tags: []
sources: []
related: []
origin: "https://example.test/source"
ingest_status: succeeded
media: []
---

"""

TARGET_FRONTMATTER = """---
author: kb-bot
created: 2026-09-07
modified: 2026-09-07
type: concept
topic: example
title: Target Concept
description: An existing target concept.
status: draft
tags: []
sources: []
related: []
---

# Target Concept
"""


def load_module():
    spec = importlib.util.spec_from_file_location("kb_repair_source_wikilinks_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path, body: str, *, frontmatter: str = SOURCE_FRONTMATTER):
    root = tmp_path / "knowledgebase"
    source = root / "topics" / "example" / "sources" / "source.md"
    target = root / "topics" / "example" / "notes" / "concepts" / "target.md"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text(frontmatter + body, encoding="utf-8")
    target.write_text(TARGET_FRONTMATTER, encoding="utf-8")
    return root, source, target


def invoke(repair, root: Path, source: Path, target: Path, *extra: str) -> int:
    relative_source = source.relative_to(root).as_posix()
    target_link = "knowledgebase/" + target.relative_to(root).as_posix().removesuffix(".md")
    return repair.main(
        [
            "--file",
            relative_source,
            "--replace",
            f"old-target={target_link}",
            *extra,
        ]
    )


def test_dry_run_is_locked_and_leaves_source_unchanged(tmp_path: Path, monkeypatch, capsys) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = source.read_bytes()
    lock_calls: list[Path] = []
    original_lock = repair._write_guard.write_lock

    @contextmanager
    def tracked_lock(*args, **kwargs):
        lock_calls.append(Path(args[0]))
        with original_lock(*args, **kwargs) as descriptor:
            yield descriptor

    monkeypatch.setattr(repair._write_guard, "write_lock", tracked_lock)

    assert invoke(repair, root, source, target, "--json") == 0
    result = json.loads(capsys.readouterr().out)

    assert lock_calls == [root]
    assert result["applied"] is False
    assert result["changes"] == [
        {
            "from": "old-target",
            "to": "knowledgebase/topics/example/notes/concepts/target",
            "suffix": "",
        }
    ]
    assert source.read_bytes() == before


def test_apply_changes_body_only_preserves_frontmatter_and_mode(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    root, source, target = make_kb(
        tmp_path,
        "See [[old-target#Evidence|Target label]].\n",
        frontmatter=SOURCE_FRONTMATTER.replace(
            "related: []", 'related: ["[[old-target]]"]'
        ),
    )
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    source.chmod(0o640)
    before_frontmatter, _ = repair._frontmatter_parts(source.read_text(encoding="utf-8"))

    assert invoke(repair, root, source, target, "--apply") == 0

    updated = source.read_text(encoding="utf-8")
    after_frontmatter, body = repair._frontmatter_parts(updated)
    assert after_frontmatter == before_frontmatter
    assert body == "\nSee [[knowledgebase/topics/example/notes/concepts/target#Evidence|Target label]].\n"
    assert stat.S_IMODE(source.stat().st_mode) == 0o640
    assert not list(source.parent.glob(".kb-repair-source-wikilinks-*"))


def test_code_quotes_and_media_embeds_are_not_navigation(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    tick = chr(96)
    fence = tick * 3
    body = (
        "> [[old-target]]\n"
        "lazy quote continuation [[old-target]]\n"
        "\n"
        + tick + "[[old-target]]" + tick + "\n"
        + fence + "markdown\n"
        + "[[old-target]]\n"
        + fence + "\n"
        + "![[old-target]]\n"
        + tick + "[[old-target]]\n"
        + "multiline inline code [[old-target]]" + tick + "\n"
        + "Ordinary [[old-target|label]] and [[old-target#Heading]].\n"
        + "    indented code [[old-target]]\n"
        + "\t tabbed code [[old-target]]\n"
    )
    root, source, target = make_kb(tmp_path, body)
    monkeypatch.setattr(repair, "repo_root", lambda: root)

    assert invoke(repair, root, source, target, "--apply") == 0

    expected_body = (
        "> [[old-target]]\n"
        "lazy quote continuation [[old-target]]\n"
        "\n"
        + tick + "[[old-target]]" + tick + "\n"
        + fence + "markdown\n"
        + "[[old-target]]\n"
        + fence + "\n"
        + "![[old-target]]\n"
        + tick + "[[old-target]]\n"
        + "multiline inline code [[old-target]]" + tick + "\n"
        + "Ordinary [[knowledgebase/topics/example/notes/concepts/target|label]] "
        + "and [[knowledgebase/topics/example/notes/concepts/target#Heading]].\n"
        + "    indented code [[old-target]]\n"
        + "\t tabbed code [[old-target]]\n"
    )
    assert source.read_text(encoding="utf-8") == SOURCE_FRONTMATTER + expected_body


def test_fence_close_requires_matching_length_and_trailing_whitespace(
    tmp_path: Path, monkeypatch
) -> None:
    repair = load_module()
    tick = chr(96)
    body = (
        tick * 4 + "markdown\n"
        + "[[old-target]]\n"
        + tick * 3 + "not-a-close\n"
        + "[[old-target]]\n"
        + tick * 3 + "\n"
        + "[[old-target]]\n"
        + tick * 4 + "\n"
        + "Ordinary [[old-target]].\n"
    )
    root, source, target = make_kb(tmp_path, body)
    monkeypatch.setattr(repair, "repo_root", lambda: root)

    assert invoke(repair, root, source, target, "--apply") == 0

    expected = (
        tick * 4 + "markdown\n"
        + "[[old-target]]\n"
        + tick * 3 + "not-a-close\n"
        + "[[old-target]]\n"
        + tick * 3 + "\n"
        + "[[old-target]]\n"
        + tick * 4 + "\n"
        + "Ordinary [[knowledgebase/topics/example/notes/concepts/target]].\n"
    )
    assert source.read_text(encoding="utf-8") == SOURCE_FRONTMATTER + expected


def test_frontmatter_only_match_is_rejected_without_edit(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    root, source, target = make_kb(
        tmp_path,
        "No eligible body link.\n",
        frontmatter=SOURCE_FRONTMATTER.replace(
            "related: []", 'related: ["[[old-target]]"]'
        ),
    )
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = source.read_bytes()

    with pytest.raises(SystemExit) as exc:
        invoke(repair, root, source, target)

    assert exc.value.code == 1
    assert source.read_bytes() == before


def test_source_path_must_be_topic_source_note(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    bad = root / "topics" / "example" / "notes" / "concepts" / "not-source.md"
    bad.write_text(SOURCE_FRONTMATTER + "See [[old-target]].\n", encoding="utf-8")
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    target_link = "knowledgebase/" + target.relative_to(root).as_posix().removesuffix(".md")

    with pytest.raises(SystemExit) as exc:
        repair.main(
            [
                "--file",
                bad.relative_to(root).as_posix(),
                "--replace",
                f"old-target={target_link}",
            ]
        )

    assert exc.value.code == 2
    assert bad.read_text(encoding="utf-8") == SOURCE_FRONTMATTER + "See [[old-target]].\n"
    assert source.read_text(encoding="utf-8") == SOURCE_FRONTMATTER + "See [[old-target]].\n"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_source_must_be_regular_singly_linked(tmp_path: Path, monkeypatch, kind: str) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    outside = tmp_path / "outside-source.md"
    outside.write_bytes(source.read_bytes())
    if kind == "symlink":
        source.unlink()
        source.symlink_to(outside)
    else:
        source.unlink()
        os.link(outside, source)
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = outside.read_bytes()

    with pytest.raises(SystemExit) as exc:
        invoke(repair, root, source, target)

    assert exc.value.code == 2
    assert outside.read_bytes() == before


@pytest.mark.parametrize("target_kind", ["symlink", "hardlink"])
def test_replacement_must_be_regular_singly_linked(tmp_path: Path, monkeypatch, target_kind: str) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    outside = tmp_path / "outside-target.md"
    outside.write_bytes(target.read_bytes())
    if target_kind == "symlink":
        target.unlink()
        target.symlink_to(outside)
    else:
        target.unlink()
        os.link(outside, target)
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = source.read_bytes()

    with pytest.raises(SystemExit) as exc:
        invoke(repair, root, source, target)

    assert exc.value.code == 2
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "target_text",
    [
        "not frontmatter\n",
        TARGET_FRONTMATTER.replace("author: kb-bot", "author: human"),
        TARGET_FRONTMATTER.replace("type: concept", "type: [concept]"),
        "---\nauthor: kb-bot\nauthor: human\ntype: concept\n---\n",
    ],
)
def test_replacement_requires_unambiguous_kb_identity(
    tmp_path: Path, monkeypatch, target_text: str
) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    target.write_text(target_text, encoding="utf-8")
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = source.read_bytes()

    with pytest.raises(SystemExit) as exc:
        invoke(repair, root, source, target)

    assert exc.value.code == 2
    assert source.read_bytes() == before


def test_source_requires_unambiguous_kb_identity(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    source.write_text(
        SOURCE_FRONTMATTER.replace("author: kb-bot", "author: human") + "See [[old-target]].\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(repair, "repo_root", lambda: root)
    before = source.read_bytes()

    with pytest.raises(SystemExit) as exc:
        invoke(repair, root, source, target)

    assert exc.value.code == 2
    assert source.read_bytes() == before


def test_replacement_path_must_be_full_and_inside_kb(tmp_path: Path, monkeypatch) -> None:
    repair = load_module()
    root, source, target = make_kb(tmp_path, "See [[old-target]].\n")
    monkeypatch.setattr(repair, "repo_root", lambda: root)

    with pytest.raises(SystemExit) as relative_exc:
        repair.main(
            [
                "--file",
                source.relative_to(root).as_posix(),
                "--replace",
                "old-target=topics/example/notes/concepts/target",
            ]
        )
    assert relative_exc.value.code == 2

    with pytest.raises(SystemExit) as escape_exc:
        repair.main(
            [
                "--file",
                source.relative_to(root).as_posix(),
                "--replace",
                "old-target=knowledgebase/../outside",
            ]
        )
    assert escape_exc.value.code == 2
