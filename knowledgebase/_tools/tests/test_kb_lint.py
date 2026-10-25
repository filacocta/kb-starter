from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-lint.py"


@pytest.mark.parametrize('topic', ['missing', '../../outside', '../outside', 'demo/../other'])
def test_cli_rejects_missing_or_escaping_topic_scope(tmp_path, topic):
    root = tmp_path / 'kb'
    (root / 'topics/demo').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'sentinel.md').write_text('# Outside scope\n')
    result = subprocess.run([sys.executable, str(SCRIPT), '--kb-root', str(root),
                             '--topic', topic, '--check', 'frontmatter'], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout
    assert 'error' in json.loads(result.stdout)
    assert 'sentinel.md' not in result.stdout


def test_cli_rejects_symlinked_topic_scope(tmp_path):
    root = tmp_path / 'kb'
    (root / 'topics').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'topics/linked').symlink_to(outside, target_is_directory=True)
    result = subprocess.run([sys.executable, str(SCRIPT), '--kb-root', str(root),
                             '--topic', 'linked', '--check', 'frontmatter'], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout


def test_cli_rejects_project_check_under_topic_scope(tmp_path):
    (tmp_path / 'topics/demo').mkdir(parents=True)
    result = subprocess.run([sys.executable, str(SCRIPT), '--kb-root', str(tmp_path),
                             '--topic', 'demo', '--check', 'projects'], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout


def test_cli_limits_project_checks_and_reports_checked_files(tmp_path):
    project = tmp_path / 'projects/demo'
    project.mkdir(parents=True)
    (project / '_summary.md').write_text('---\nauthor: kb-bot\ncreated: 2026-01-01\nmodified: 2026-01-01\ntype: meta\n---\n# Demo\n')
    other = tmp_path / 'projects/other'
    other.mkdir()
    (other / '_summary.md').write_text('# Missing metadata\n')
    result = subprocess.run([sys.executable, str(SCRIPT), '--kb-root', str(tmp_path),
                             '--project', 'demo', '--check', 'frontmatter'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['files_checked'] == 1 and report['scope'] == 'demo'
    assert report['scope_kind'] == 'project' and not report['findings']


def test_cli_reports_an_existing_empty_topic_without_claiming_files_checked(tmp_path):
    (tmp_path / 'topics/demo').mkdir(parents=True)
    result = subprocess.run([sys.executable, str(SCRIPT), '--kb-root', str(tmp_path),
                             '--topic', 'demo', '--check', 'frontmatter'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['files_checked'] == 0


def load_lint_module():
    spec = importlib.util.spec_from_file_location("kb_lint", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def init_lint_repo(path: Path, *, commit_date: str = "2026-09-10T10:00:00+00:00") -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = commit_date
    env["GIT_COMMITTER_DATE"] = commit_date
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "user.name=KB fixture",
            "-c", "user.email=kb-fixture@example.invalid", "commit", "-qm", "fixture commit",
        ],
        check=True,
        env=env,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_lint_project(
    kb_root: Path,
    *,
    repo: Path | None = None,
    modified: str = "2026-09-10",
    extra_frontmatter: str = "",
    todos: str | None = None,
) -> Path:
    project = kb_root / "projects" / "example"
    project.mkdir(parents=True)
    location = f"\n**Code location:** `{repo}`\n" if repo is not None else ""
    (project / "_summary.md").write_text(
        "---\n"
        "author: kb-bot\n"
        "created: 2026-09-01\n"
        f"modified: {modified}\n"
        "type: summary\n"
        "topic: example\n"
        "title: Example\n"
        "description: Example project.\n"
        "status: active\n"
        f"{extra_frontmatter}"
        "---\n\n"
        "# Example\n"
        f"{location}",
        encoding="utf-8",
    )
    (project / "_todos.md").write_text(
        todos
        or "## 🗂️ Backlog\n\n## 🔄 In Progress\n\n## 🧪 Testing / Review\n\n## ✅ Done\n",
        encoding="utf-8",
    )
    return project


def code_state(repo: Path, commit: str, *, dirty: bool = False, observed_at: str = "2026-09-10T09:00:00Z") -> str:
    return (
        f"code_state_repo_id: {(repo / '.git').resolve()}\n"
        f"code_state_commit: {commit}\n"
        f"code_state_dirty: {'true' if dirty else 'false'}\n"
        f"code_state_observed_at: {observed_at}\n"
        "code_state_scope: repository-git-metadata\n"
    )


def alias_fixture(root):
    (root / '_tools').mkdir(parents=True)
    shutil.copyfile(ROOT / '_tools/kb-seen.py', root / '_tools/kb-seen.py')
    shutil.copyfile(ROOT / '_tools/kb-write-guard.py', root / '_tools/kb-write-guard.py')
    source = root / 'topics/example/sources/source.md'
    source.parent.mkdir(parents=True)
    source.write_text('---\nauthor: kb-bot\ntype: source\ningest_status: succeeded\norigin: https://old.example/article\n---\n# Source\n')
    (root / '_url-aliases.json').write_text(json.dumps(dict(author='kb-bot', version=1, entries=[dict(
        alias='https://new.example/article', source_note='topics/example/sources/source.md',
        evidence='Human confirmed exact moved page, fixture only', verified='2026-09-06')])) )
    return root / 'topics/example/reading-list.md'


def test_lint_governed_alias_rows_are_same_source_identity(tmp_path):
    lint = load_lint_module()
    queue = alias_fixture(tmp_path)
    target = '[[knowledgebase/topics/example/sources/source]]'
    queue.write_text(f'## Approved\n- [x] https://old.example/article — {target}\n- [x] https://new.example/article — {target}\n')
    findings = []
    lint.check_reading_lists(tmp_path, 'example', findings)
    assert not any(f['code'] in {'source-note-multiple-reading-list-rows', 'url-alias-registry'} for f in findings)


def test_lint_reports_invalid_alias_registry(tmp_path):
    lint = load_lint_module()
    alias_fixture(tmp_path)
    (tmp_path / '_url-aliases.json').write_text('{broken')
    findings = []
    lint.check_reading_lists(tmp_path, 'example', findings)
    assert any(f['code'] == 'url-alias-registry' and f['severity'] == 'error' for f in findings)


def test_topic_summary_size_warning_is_read_only_and_excludes_frontmatter(tmp_path):
    lint = load_lint_module()
    summary = tmp_path / "topics/example/_summary.md"
    summary.parent.mkdir(parents=True)
    header = "---\nauthor: kb-bot\ntype: summary\ndescription: " + "metadata " * 400 + "\n---\n"
    for word_count in (299, 300, 301):
        summary.write_text(header + "word " * word_count)
        before = summary.read_bytes()
        findings = []
        lint.check_frontmatter(tmp_path, [summary], findings)
        warnings = [f for f in findings if f["code"] == "topic-summary-too-long"]
        assert len(warnings) == (1 if word_count >= 300 else 0)
        assert summary.read_bytes() == before


def test_project_summary_size_warning_is_scoped_and_not_duplicated(tmp_path):
    lint = load_lint_module()
    project_summary = tmp_path / "projects/example/_summary.md"
    project_summary.parent.mkdir(parents=True)
    project_summary.write_text(
        "---\nauthor: kb-bot\ntype: summary\ndescription: project\n---\n"
        + "word " * 300
    )

    findings = []
    lint.check_frontmatter(tmp_path, [project_summary], findings)

    warnings = [f for f in findings if f["code"] == "topic-summary-too-long"]
    assert len(warnings) == 1
    assert warnings[0]["path"] == "projects/example/_summary.md"
    assert "project summary body" in warnings[0]["message"]


def test_summary_size_scope_is_topic_local_and_all_includes_projects(tmp_path):
    lint = load_lint_module()
    topic_summary = tmp_path / "topics/example/_summary.md"
    project_summary = tmp_path / "projects/example/_summary.md"
    for path in (topic_summary, project_summary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nauthor: kb-bot\ntype: summary\ndescription: fixture\n---\n"
            + "word " * 300
        )

    topic_findings = []
    lint.check_frontmatter(tmp_path, lint.iter_md_files(tmp_path, "example"), topic_findings)
    topic_paths = {
        finding["path"]
        for finding in topic_findings
        if finding["code"] == "topic-summary-too-long"
    }
    assert topic_paths == {"topics/example/_summary.md"}

    all_findings = []
    lint.check_frontmatter(tmp_path, lint.iter_md_files(tmp_path, None), all_findings)
    all_paths = {
        finding["path"]
        for finding in all_findings
        if finding["code"] == "topic-summary-too-long"
    }
    assert all_paths == {"topics/example/_summary.md", "projects/example/_summary.md"}
    assert len(all_paths) == 2


def test_summary_limit_does_not_flag_history_or_source_notes(tmp_path):
    lint = load_lint_module()
    files = [tmp_path / "projects/example/notes/summary-history.md", tmp_path / "topics/example/sources/source.md"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nauthor: kb-bot\ntype: meta\n---\n" + "word " * 400)
    findings = []
    lint.check_frontmatter(tmp_path, files, findings)
    assert not any(f["code"] == "topic-summary-too-long" for f in findings)


def write_seen_stub(kb_root: Path) -> None:
    tools = kb_root / "_tools"
    tools.mkdir(parents=True)
    (tools / "kb-seen.py").write_text(
        """
def load_index(kb_root):
    return {}

def normalize(url):
    return url.rstrip("/")

def is_ingested(url, kb_root, seen_index=None):
    if "already-ingested" in url:
        return "topics/example/sources/already-ingested.md"
    return None
""".lstrip(),
        encoding="utf-8",
    )


def test_checked_auto_approved_ingested_item_does_not_warn(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        """## Auto-approved

- [x] https://example.com/already-ingested — ingested already
- [ ] https://example.com/already-ingested-open — still incorrectly open
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    warnings = [finding for finding in findings if finding["code"] == "auto-approved-already-ingested"]
    assert len(warnings) == 1
    assert warnings[0]["line"] == 4


def test_global_inbox_unchecked_ingested_item_warns_with_cleanup_command(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    links = kb_root / "readinglist" / "Links.md"
    links.parent.mkdir(parents=True)
    write_seen_stub(kb_root)
    links.write_text(
        """# URL Inbox

- [x] https://example.com/already-ingested-checked — already handled
- [ ] https://example.com/already-ingested-open?s=20 — still incorrectly open
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    warnings = [finding for finding in findings if finding["code"] == "global-inbox-already-ingested"]
    assert len(warnings) == 1
    assert warnings[0]["path"] == "readinglist/Links.md"
    assert warnings[0]["line"] == 4
    message = str(warnings[0]["message"])
    assert "[[knowledgebase/topics/example/sources/already-ingested]]" in message
    assert "Cleanup: ./_tools/kb-checkoff.py --file readinglist/Links.md" in message
    assert "--mark x" in message
    assert warnings[0]["url"] == "https://example.com/already-ingested-open?s=20"
    assert warnings[0]["source_note"] == "topics/example/sources/already-ingested.md"
    cleanup = str(warnings[0]["cleanup_command"])
    assert cleanup.startswith("./_tools/kb-checkoff.py --file readinglist/Links.md")
    assert "--note 'already ingested -> [[knowledgebase/topics/example/sources/already-ingested]]'" in cleanup


def test_topic_scoped_reading_list_lint_skips_global_inbox(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    links = kb_root / "readinglist" / "Links.md"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    links.parent.mkdir(parents=True)
    write_seen_stub(kb_root)
    links.write_text(
        "- [ ] https://example.com/already-ingested-open — still incorrectly open\n",
        encoding="utf-8",
    )
    (topic / "reading-list.md").write_text("## Pending\n\n", encoding="utf-8")

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, "example", findings)

    assert [finding for finding in findings if finding["code"] == "global-inbox-already-ingested"] == []


def test_library_payload_exempt_from_frontmatter_checks(tmp_path: Path) -> None:
    """Raw extraction dumps under _library/{books,docs}/ (e.g. *.ocr.md) are custody
    payload, exempt from kb-bot frontmatter; `_`-prefixed control files are not."""
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    books = kb_root / "_library" / "books" / "design-css"
    books.mkdir(parents=True)
    (books / "sometitle.ocr.md").write_text("<!-- page 0 -->\nraw OCR text\n", encoding="utf-8")
    (books / "_README.md").write_text("---\nauthor: kb-bot\n---\n# Books\n", encoding="utf-8")

    assert lint.is_library_payload(books / "sometitle.ocr.md", kb_root) is True
    assert lint.is_library_payload(books / "_README.md", kb_root) is False
    assert lint.is_library_payload(kb_root / "_library" / "source-custody-index.md", kb_root) is False

    files = lint.iter_md_files(kb_root, None)
    names = {p.name for p in files}
    assert "sometitle.ocr.md" not in names  # payload skipped
    assert "_README.md" in names            # control file still linted


def test_captured_image_media_note_requires_ocr_and_vision_sections(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    note = kb_root / "topics" / "example" / "notes" / "media" / "image.md"
    media_file = kb_root / "_media" / "images" / "2026" / "07" / "image.png"
    note.parent.mkdir(parents=True)
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"not-really-a-png")
    note.write_text(
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: media-description
topic: example
title: Image
description: Test image.
status: draft
media_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
media_kind: image
media_status: captured
local_path: "knowledgebase/_media/images/2026/07/image.png"
---

# Image
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_media(kb_root, [note], findings)

    codes = {finding["code"] for finding in findings}
    assert "media-missing-ocr" in codes
    assert "media-missing-vision-description" in codes


def test_duplicate_detection_uses_primary_checklist_url_only(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        """## Failed -- Retry

- [!] https://old.example/path — try replacement: https://replacement.example/doc

## Pending

- [ ] https://replacement.example/doc — replacement source
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    assert [finding for finding in findings if finding["code"] == "reading-list-duplicate-url-across-sections"] == []


def test_source_frontmatter_with_multiple_origin_urls_warns(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    source = kb_root / "topics" / "example" / "sources" / "batch.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        """---
author: kb-bot
created: 2026-06-29
modified: 2026-06-29
type: source
topic: example
title: Batch
description: Bad batch note.
status: draft
origin: "https://example.com/a; https://example.com/b"
---

# Batch
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_frontmatter(kb_root, [source], findings)

    assert [finding for finding in findings if finding["code"] == "source-origin-multiple-urls"]


def test_multiple_completed_rows_pointing_to_one_source_warns(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        """## Auto-approved

- [x] https://example.com/a — ingested 2026-06-29 -> [[knowledgebase/topics/example/sources/bad-batch]]
- [x] https://example.com/b — ingested 2026-06-29 -> [[knowledgebase/topics/example/sources/bad-batch]]
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    warnings = [finding for finding in findings if finding["code"] == "source-note-multiple-reading-list-rows"]
    assert len(warnings) == 1
    assert warnings[0]["line"] == 3


def test_contextual_source_wikilinks_do_not_count_as_completed_targets(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        """## Pending

- [x] https://example.com/a — triage closed: redundant mirror of already-ingested [[knowledgebase/topics/example/sources/canonical]]
- [x] https://example.com/b — triage closed: redundant mirror of already-ingested [[knowledgebase/topics/example/sources/canonical]]
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    assert [finding for finding in findings if finding["code"] == "source-note-multiple-reading-list-rows"] == []


def test_multiple_completed_rows_pointing_to_one_legacy_source_shorthand_warns(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        """## Processed

- [x] https://example.com/a — → sources/bad-batch
- [x] https://example.com/b — → sources/bad-batch
""",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, None, findings)

    warnings = [finding for finding in findings if finding["code"] == "source-note-multiple-reading-list-rows"]
    assert len(warnings) == 1
    assert warnings[0]["message"].startswith("multiple completed reading-list URLs point to one source note")


def test_reading_list_lint_ignores_markdown_examples_and_annotation_urls(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    text = (
        "## Pending\n"
        "```markdown\n## Auto-approved\n"
        "- [ ] https://example.com/already-ingested — fake fenced approval\n```\n"
        "> - [ ] https://example.com/already-ingested — fake quote\n"
        "    - [ ] https://example.com/already-ingested — fake indent\n"
        "<!--\n- [ ] https://example.com/already-ingested — fake comment\n-->\n"
        "- [ ] https://example.com/already-ingested — mentions https://example.com/target\n"
    )
    # Keep the fixture deliberately CRLF-terminated at neither end; parser
    # line positions and exact primary locators must not depend on either.
    (topic / "reading-list.md").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, "example", findings)

    assert not any(f["code"] == "auto-approved-already-ingested" for f in findings)
    assert not any(f["code"] == "reading-list-duplicate-url-across-sections" for f in findings)
    assert not any("https://example.com/target" == f.get("url") for f in findings)


def test_reading_list_lint_reports_duplicate_approval_structure_without_granting_approval(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        "## Approved\n"
        "- [ ] https://example.com/same\n\n"
        "## Approved\n"
        "- [ ] https://example.com/same\n",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, "example", findings)

    assert any(f["code"] == "reading-list-duplicate-section" for f in findings)
    # The duplicate is within one ambiguous section name, so lint reports the
    # structure but does not misclassify it as a clean cross-section approval.
    assert not any(f["code"] == "auto-approved-already-ingested" for f in findings)


def test_reading_list_lint_does_not_treat_malformed_approval_heading_as_active(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    topic = kb_root / "topics" / "example"
    topic.mkdir(parents=True)
    write_seen_stub(kb_root)
    (topic / "reading-list.md").write_text(
        "## auto-approved\n"
        "- [ ] https://example.com/already-ingested\n",
        encoding="utf-8",
    )

    findings: list[dict[str, object]] = []
    lint.check_reading_lists(kb_root, "example", findings)

    assert any(f["code"] == "reading-list-noncanonical-section" for f in findings)
    assert not any(f["code"] == "auto-approved-already-ingested" for f in findings)


def test_project_lint_keeps_absent_code_state_unknown_and_accepts_board_variants(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    write_lint_project(
        kb_root,
        todos=(
            "## 🗂️ Backlog\n- [ ] backlog item\n\n"
            "## In Progress\n- [~] active item\n\n"
            "## 🧪 Testing/Review\n- [x] checked item\n\n"
            "## ✅ Done\n- [!] blocked history\n"
        ),
    )
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    codes = {str(finding["code"]) for finding in findings}
    assert not any(code.startswith("project-code-state-") for code in codes)
    assert not any(code.startswith("project-todos-") for code in codes)


def test_project_todo_lint_is_advisory_and_ignores_examples(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    write_lint_project(
        kb_root,
        todos=(
            "## Backlog\n- [z] unsupported marker\n\n"
            "<!-- ## In Progress\n- [z] commented example -->\n"
            "```markdown\n## Testing / Review\n- [z] fenced example\n"
            "<!-- literal comment in fenced example -->\n```\n"
            "> ## ✅ Done\n> - [z] quoted example\n"
        ),
    )
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    codes = [str(finding["code"]) for finding in findings]
    assert codes.count("project-todos-missing-section") == 3
    assert codes.count("project-todos-invalid-checkbox") == 1


def test_project_lint_labels_document_heuristic_and_code_state_divergence(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    repo = tmp_path / "repo"
    commit = init_lint_repo(repo)
    write_lint_project(
        kb_root,
        repo=repo,
        modified="2026-07-01",
        extra_frontmatter=code_state(repo, "b" * 40),
    )
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    by_code = {str(finding["code"]): finding for finding in findings}
    assert "project-summary-behind-code" in by_code
    assert "document-date heuristic" in str(by_code["project-summary-behind-code"]["message"])
    assert "implementation verification" in str(by_code["project-summary-behind-code"]["message"])
    assert "project-code-state-diverged" in by_code
    assert commit != "b" * 40


def test_project_lint_partial_current_git_observation_is_uncheckable(tmp_path: Path, monkeypatch) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    repo = tmp_path / "repo"
    commit = init_lint_repo(repo)
    write_lint_project(kb_root, repo=repo, extra_frontmatter=code_state(repo, commit))
    project_status = lint._load_project_status()
    original = project_status._git_run

    def fail_status(path, args, timeout=5):
        if args and args[0] == "status":
            return 1, "", "status fixture failure"
        return original(path, args, timeout)

    monkeypatch.setattr(project_status, "_git_run", fail_status)
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    uncheckable = [f for f in findings if f["code"] == "project-code-state-uncheckable"]
    assert len(uncheckable) == 1
    assert "status fixture failure" in str(uncheckable[0]["message"])


def test_project_lint_does_not_use_parent_repository_for_child_location(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    repo = tmp_path / "repo"
    init_lint_repo(repo)
    child = repo / "src"
    child.mkdir()
    write_lint_project(kb_root, repo=child)
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    location_findings = [f for f in findings if f["code"] == "project-code-location"]
    assert len(location_findings) == 1
    assert "parent-repository false positive" in str(location_findings[0]["message"])


def test_project_lint_rejects_malformed_code_state_using_shared_parser(tmp_path: Path) -> None:
    lint = load_lint_module()
    kb_root = tmp_path / "kb"
    write_lint_project(
        kb_root,
        extra_frontmatter=(
            "code_state_repo_id: /tmp/repo/.git\n"
            "code_state_commit: " + "a" * 40 + "\n"
            'code_state_dirty: "false"\n'
            "code_state_dirty: false\n"
            "  nested: value\n"
            "code_state_observed_at: 2026-09-10T09:00:00Z\n"
            "code_state_scope: repository-git-metadata\n"
        ),
    )
    findings: list[dict[str, object]] = []
    lint.check_projects(kb_root, findings, stale_days=21, topic=None)
    invalid = [f for f in findings if f["code"] == "project-code-state-invalid"]
    assert len(invalid) == 1
    message = str(invalid[0]["message"])
    assert "duplicate frontmatter key" in message
    assert "actual YAML boolean" in message


def test_explicit_project_check_selection_is_not_all_by_default():
    lint = load_lint_module()
    args = lint.build_parser().parse_args(["--check", "projects"])
    assert args.check == ["projects"]
