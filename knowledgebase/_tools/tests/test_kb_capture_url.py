from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-capture-url.py"
FRONTMATTER = "---\nauthor: kb-bot\ntype: meta\nmodified: 2026-09-06\n---\n"


def load_capture_module():
    spec = importlib.util.spec_from_file_location("kb_capture_url", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_kb(tmp_path: Path, *, topic: bool = False) -> tuple[Path, Path, Path | None]:
    kb_root = tmp_path / "kb"
    links = kb_root / "readinglist" / "Links.md"
    links.parent.mkdir(parents=True)
    links.write_text(FRONTMATTER + "# Links\n\n", encoding="utf-8")
    topic_rl = None
    if topic:
        topic_rl = kb_root / "topics" / "example" / "reading-list.md"
        topic_rl.parent.mkdir(parents=True)
        topic_rl.write_text(
            FRONTMATTER
            + "# Example — Reading List\n\n"
            + "## Pending\n\n"
            + "## Approved\n\n"
            + "## Auto-approved\n\n"
            + "## Processed\n\n"
            + "## Failed — Retry\n",
            encoding="utf-8",
        )
    return kb_root, links, topic_rl


def write_seen(kb_root: Path, url: str, value=None, *, body: str | None = None, exit_code: int = 0) -> None:
    seen = kb_root / "_tools" / "kb-seen.py"
    seen.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = json.dumps({"ingested": {url: value}})
    seen.write_text(
        "import sys\n"
        f"sys.stdout.write({body!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )


def run_capture(mod, kb_root: Path, url: str, *extra: str) -> int:
    return mod.main([url, "--kb-root", str(kb_root), *extra])


def test_capture_skips_fenced_pending_heading_and_duplicate_example(tmp_path):
    mod = load_capture_module()
    root, _, queue = make_kb(tmp_path, topic=True)
    url = "https://example.test/new"
    write_seen(root, url)
    example = f"~~~markdown\n## Pending\n- [ ] {url}\n~~~\n"
    queue.write_text(queue.read_text().replace("## Pending\n", example + "## Pending\n", 1))
    assert run_capture(mod, root, url, "--topic", "example") == 0
    text = queue.read_text()
    assert example in text
    rows = mod.queue_parser().parse_queue(text).rows
    assert [row.section for row in rows if row.locator == url] == ["Pending"]


@pytest.mark.parametrize("fragment", ["## Pending\n", "## pending\n", "~~~\nunfinished\n"])
def test_capture_refuses_ambiguous_or_unclosed_topic_structure(tmp_path, fragment):
    mod = load_capture_module()
    root, _, queue = make_kb(tmp_path, topic=True)
    url = "https://example.test/new"
    write_seen(root, url)
    text = queue.read_text() + fragment
    queue.write_text(text)
    assert run_capture(mod, root, url, "--topic", "example") != 0
    assert queue.read_text() == text


def test_capture_global_inbox_does_not_append_inside_unclosed_example(tmp_path):
    mod = load_capture_module()
    root, links, _ = make_kb(tmp_path)
    url = "https://example.test/new"
    write_seen(root, url)
    text = links.read_text() + "```markdown\n"
    links.write_text(text)
    assert run_capture(mod, root, url) != 0
    assert links.read_text() == text


@pytest.mark.parametrize("topic", [False, True])
def test_capture_note_cannot_open_hidden_comment_block(tmp_path, topic):
    mod = load_capture_module()
    root, links, queue = make_kb(tmp_path, topic=topic)
    url = "https://example.test/new"
    write_seen(root, url)
    target = queue if topic else links
    before = target.read_text()
    extra = ["--topic", "example"] if topic else []
    assert run_capture(mod, root, url, *extra, "--note", "example <!--") != 0
    assert target.read_text() == before


def test_capture_uses_exact_url_matching_and_preserves_mode(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    url = "https://example.test/item"
    links.write_text(
        links.read_text(encoding="utf-8")
        + "- [ ] https://example.test/item-extra — existing\n",
        encoding="utf-8",
    )
    links.chmod(0o664)
    write_seen(kb_root, url)

    assert run_capture(mod, kb_root, url, "--source", "agent test") == 0

    text = links.read_text(encoding="utf-8")
    assert "https://example.test/item-extra" in text
    assert f"- [ ] {url} — (via agent test, " in text
    assert links.stat().st_mode & 0o777 == 0o664
    assert not list(links.parent.glob("*.tmp"))


def test_capture_routes_valid_topic_to_pending(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, topic_rl = make_kb(tmp_path, topic=True)
    assert topic_rl is not None
    url = "https://example.test/topic"
    write_seen(kb_root, url)

    assert run_capture(mod, kb_root, url, "--topic", "example") == 0

    topic_text = topic_rl.read_text(encoding="utf-8")
    assert f"- [ ] {url} — (via agent, " in topic_text
    assert url not in links.read_text(encoding="utf-8")


def test_explicit_missing_topic_fails_without_global_fallback(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    before = links.read_bytes()
    url = "https://example.test/missing-topic"
    write_seen(kb_root, url)

    assert run_capture(mod, kb_root, url, "--topic", "missing") == 1
    assert links.read_bytes() == before


@pytest.mark.parametrize("topic", ["../example", "foo/bar", ".."])
def test_invalid_topic_slug_fails_without_global_fallback(tmp_path: Path, topic: str) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    before = links.read_bytes()
    url = "https://example.test/invalid-topic"
    write_seen(kb_root, url)

    assert run_capture(mod, kb_root, url, "--topic", topic) == 2
    assert links.read_bytes() == before


def test_capture_rejects_symlinked_topic_parent(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, topic_rl = make_kb(tmp_path, topic=True)
    assert topic_rl is not None
    alias = kb_root / "topics" / "alias"
    alias.symlink_to(topic_rl.parent, target_is_directory=True)
    url = "https://example.test/symlink-parent"
    write_seen(kb_root, url)
    before = topic_rl.read_bytes()

    assert run_capture(mod, kb_root, url, "--topic", "alias") == 1
    assert topic_rl.read_bytes() == before
    assert url not in links.read_text(encoding="utf-8")


def test_capture_dedup_missing_helper_fails_closed(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    before = links.read_bytes()

    assert run_capture(mod, kb_root, "https://example.test/no-helper") == 1
    assert links.read_bytes() == before


@pytest.mark.parametrize(
    ("body", "exit_code"),
    [("not-json", 0), (json.dumps({"unexpected": {}}), 0), ("", 2),
     (json.dumps({"ingested": {"https://example.test/bad-dedup": None}, "error": "uncertain"}), 0),
     (json.dumps({"ingested": {"https://example.test/bad-dedup": None, "https://other.test": None}}), 0)],
)
def test_capture_dedup_invalid_result_fails_closed(
    tmp_path: Path, body: str, exit_code: int
) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    url = "https://example.test/bad-dedup"
    before = links.read_bytes()
    write_seen(kb_root, url, body=body, exit_code=exit_code)

    assert run_capture(mod, kb_root, url) == 1
    assert links.read_bytes() == before


def test_capture_rejects_newline_injection_without_writing(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    url = "https://example.test/newline"
    write_seen(kb_root, url)
    before = links.read_bytes()

    assert run_capture(mod, kb_root, url, "--note", "safe\n- [ ] injected") == 2
    assert links.read_bytes() == before


@pytest.mark.parametrize("url", [
    "https://example.test injected",
    "https://example.test\tinjected",
    "https://example.test\x00injected",
    "https://",
])
def test_capture_rejects_malformed_http_url_without_writing(tmp_path: Path, url: str) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    before = links.read_bytes()

    assert run_capture(mod, kb_root, url) == 2
    assert links.read_bytes() == before


def test_capture_rejects_human_owned_queue_without_writing(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    links.write_text(FRONTMATTER.replace("author: kb-bot", "author: human") + "# Links\n", encoding="utf-8")
    url = "https://example.test/human"
    write_seen(kb_root, url)
    before = links.read_bytes()

    assert run_capture(mod, kb_root, url) == 1
    assert links.read_bytes() == before


def test_capture_reports_already_ingested_without_writing(tmp_path: Path) -> None:
    mod = load_capture_module()
    kb_root, links, _ = make_kb(tmp_path)
    url = "https://example.test/seen"
    write_seen(kb_root, url, "topics/example/sources/seen.md")
    before = links.read_bytes()

    assert run_capture(mod, kb_root, url) == 0
    assert links.read_bytes() == before
