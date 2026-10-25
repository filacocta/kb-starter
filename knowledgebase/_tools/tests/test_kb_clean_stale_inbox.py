from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-clean-stale-inbox.py"


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
    if url.rstrip("/") == "https://example.com/already-ingested":
        return "topics/example/sources/already-ingested.md"
    return None
""".lstrip(),
        encoding="utf-8",
    )


def write_links(kb_root: Path) -> Path:
    links = kb_root / "readinglist" / "Links.md"
    links.parent.mkdir(parents=True)
    links.write_text(
        """---
author: kb-bot
type: meta
---
# URL Inbox

- [ ] https://example.com/already-ingested — stale
- [ ] https://example.com/new — still open
""",
        encoding="utf-8",
    )
    return links


def test_clean_stale_inbox_dry_run_does_not_mutate(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"
    write_seen_stub(kb_root)
    links = write_links(kb_root)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--kb-root", str(kb_root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["apply"] is False
    assert payload["stale_count"] == 1
    assert "- [ ] https://example.com/already-ingested" in links.read_text(encoding="utf-8")


def test_clean_stale_inbox_apply_uses_checkoff(tmp_path: Path) -> None:
    kb_root = tmp_path / "kb"
    write_seen_stub(kb_root)
    links = write_links(kb_root)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--kb-root", str(kb_root), "--apply"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["apply"] is True
    assert payload["stale_count"] == 1
    assert payload["actions"][0]["returncode"] == 0
    text = links.read_text(encoding="utf-8")
    assert "- [x] https://example.com/already-ingested" in text
    assert "already ingested -> [[knowledgebase/topics/example/sources/already-ingested]]" in text
    assert "- [ ] https://example.com/new" in text
