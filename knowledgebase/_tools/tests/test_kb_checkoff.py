from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-checkoff.py"


def load_checkoff_module():
    spec = importlib.util.spec_from_file_location("kb_checkoff", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkoff_can_route_original_item_to_ideas_backlog(tmp_path: Path) -> None:
    checkoff = load_checkoff_module()
    kb_root = tmp_path / "kb"
    inbox = kb_root / "readinglist" / "topics.md"
    backlog = kb_root / "ideas" / "_backlog.md"
    inbox.parent.mkdir(parents=True)
    backlog.parent.mkdir(parents=True)
    inbox.write_text(
        "- [ ] build a tiny workflow dashboard — (via Telegram #inspiration-capture, 2026-06-16)\n",
        encoding="utf-8",
    )
    backlog.write_text("# Ideas Backlog\n\n## Backlog\n\n", encoding="utf-8")

    ok, msg = checkoff.checkoff(
        inbox,
        "workflow dashboard",
        "x",
        "routed to ideas-backlog 2026-06-16",
        kb_root / ".kb-write.lock",
        ideas_backlog=backlog,
        kb_root=kb_root,
    )

    assert ok is True
    assert "line 1" in msg
    assert "- [x] build a tiny workflow dashboard" in inbox.read_text(encoding="utf-8")
    backlog_text = backlog.read_text(encoding="utf-8")
    assert "- [ ] build a tiny workflow dashboard" in backlog_text
    assert "routed from `readinglist/topics.md` line 1" in backlog_text
