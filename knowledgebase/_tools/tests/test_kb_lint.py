from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-lint.py"


def load_lint_module():
    spec = importlib.util.spec_from_file_location("kb_lint", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
