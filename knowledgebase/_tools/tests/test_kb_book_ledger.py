from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-book-ledger.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_book_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = """---
author: kb-bot
created: 2026-09-06
modified: 2026-09-06
type: meta
title: Book Acquisition Ledger
description: Fixture ledger.
---

# Book Acquisition Ledger

Human prose and spacing must survive a targeted mutation.

## Wanted

<!-- wanted comment -->
- [ ] **Trading and Exchanges** — Harris, 2003.
      Referenced by [[knowledgebase/topics/trading/sources/source-a]].

- [ ] **Other Book** — Author, 2020.

## Proposed

<!-- proposed comment -->

## Acquired

<!-- acquired comment -->

## Ingested

<!-- ingested comment -->
"""


def make_kb(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "kb"
    ledger = root / "_library" / "books" / "_ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(LEDGER, encoding="utf-8")
    return root, ledger


def run(mod, root: Path, *args: str) -> int:
    return mod.main(["--kb-root", str(root), *args])


def test_add_and_normalized_substring_match_preserve_unrelated_prose(tmp_path: Path) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)
    before = ledger.read_text(encoding="utf-8")

    # "Other" intentionally exercises the historical substring selector
    # against the full title "Other Book".
    assert run(mod, root, "--add", "--title", "Other", "--referenced-by", "[[knowledgebase/topics/markets/sources/b]]") == 0
    updated = ledger.read_text(encoding="utf-8")
    assert "Human prose and spacing must survive a targeted mutation." in updated
    assert "Also referenced by [[knowledgebase/topics/markets/sources/b]]" in updated
    # Add a genuinely new entry as well; it belongs under Wanted.
    assert run(mod, root, "--add", "--title", "New Book", "--author", "Writer", "--year", "2026") == 0
    updated = ledger.read_text(encoding="utf-8")
    assert "- [ ] **New Book** — Writer, 2026." in updated
    # A legitimate write updates modified; prose preservation must not depend
    # on the test running on the fixture's historical date.
    before_body = before.split("---\n", 2)[2]
    assert before_body.split("Human prose", 1)[0] in updated


def test_propose_approve_and_move_are_targeted_mutations(tmp_path: Path) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)

    assert run(mod, root, "--propose", "--title", "Other Book", "--url", "https://publisher.example/book", "--url", "https://archive.org/details/book") == 0
    text = ledger.read_text(encoding="utf-8")
    assert "Other Book" in text and "Proposed: [source](https://publisher.example/book)" in text
    assert text.index("Other Book") > text.index("## Proposed")

    assert run(mod, root, "--approve", "--title", "Other Book", "--url", "https://archive.org/details/book") == 0
    text = ledger.read_text(encoding="utf-8")
    assert "Approved source (" in text
    assert text.index("Other Book") > text.index("## Acquired")

    assert run(mod, root, "--move", "--title", "Other Book", "--to", "Ingested", "--note", "source note linked") == 0
    text = ledger.read_text(encoding="utf-8")
    assert text.index("Other Book") > text.index("## Ingested")
    assert "source note linked (" in text
    assert "<!-- wanted comment -->" in text
    assert "<!-- proposed comment -->" in text


def test_list_json_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)
    before = ledger.read_bytes()
    assert run(mod, root, "--list", "--json") == 0
    assert ledger.read_bytes() == before
    assert '"Wanted"' in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["human", "symlink", "traversal"])
def test_ownership_and_canonical_path_guards(tmp_path: Path, bad: str) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)
    if bad == "human":
        ledger.write_text(LEDGER.replace("author: kb-bot", "author: human"), encoding="utf-8")
        before = ledger.read_bytes()
        assert run(mod, root, "--add", "--title", "Nope") == 2
        assert ledger.read_bytes() == before
    elif bad == "symlink":
        real = ledger.with_name("real.md")
        real.write_bytes(ledger.read_bytes())
        ledger.unlink()
        ledger.symlink_to(real)
        assert run(mod, root, "--add", "--title", "Nope") == 2
        assert real.read_bytes() == LEDGER.encode()
    else:
        traversed = root / "_library" / "books" / ".." / "books" / "_ledger.md"
        before = ledger.read_bytes()
        assert run(mod, root, "--file", str(traversed), "--add", "--title", "Nope") == 2
        assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    "option,value",
    [
        ("--title", "bad\n## Acquired"),
        ("--referenced-by", "bad\n- [ ] injected"),
        ("--note", "bad\n## Ingested"),
        ("--url", "https://example.test/book\ninjected"),
    ],
)
def test_mutation_arguments_reject_multiline_injection(tmp_path: Path, option: str, value: str) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)
    before = ledger.read_bytes()
    args = ["--add", "--title", "Safe Book", option, value] if option != "--url" else ["--propose", "--title", "Other Book", option, value]
    assert run(mod, root, *args) == 2
    assert ledger.read_bytes() == before


def test_atomic_write_preserves_mode_and_leaves_no_predictable_temp(tmp_path: Path) -> None:
    mod = load_module()
    root, ledger = make_kb(tmp_path)
    ledger.chmod(0o640)
    assert run(mod, root, "--add", "--title", "Mode Book") == 0
    assert ledger.stat().st_mode & 0o777 == 0o640
    assert not list(ledger.parent.glob("*.tmp"))
    assert not list(ledger.parent.glob(".kb-ledger-*"))
