from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-log-append.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_log_append", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_log(kb_root: Path, author: str = "kb-bot") -> None:
    kb_root.mkdir(parents=True, exist_ok=True)
    (kb_root / "_log.md").write_text(
        f"---\nauthor: {author}\ntype: meta\n---\n\n# KB Activity Log\n",
        encoding="utf-8",
    )


def test_append_is_atomic_and_idempotent(tmp_path: Path) -> None:
    module = load_module()
    kb_root = tmp_path / "knowledgebase"
    make_log(kb_root)

    appended, message = module.append_entry(
        kb_root,
        "2026-08-11-quality-hardening",
        "## [2026-08-11] workflow-update | quality hardening\n\n- Added a guard.",
    )
    assert appended is True
    assert message == "appended: 2026-08-11-quality-hardening"
    appended, message = module.append_entry(
        kb_root,
        "2026-08-11-quality-hardening",
        "## [2026-08-11] workflow-update | duplicate retry",
    )
    assert appended is False
    assert message == "already appended: 2026-08-11-quality-hardening"
    text = (kb_root / "_log.md").read_text(encoding="utf-8")
    assert text.count("kb-operation-id: 2026-08-11-quality-hardening") == 1
    assert "duplicate retry" not in text


def test_append_rejects_unowned_log_and_invalid_operation_id(tmp_path: Path) -> None:
    module = load_module()
    kb_root = tmp_path / "knowledgebase"
    make_log(kb_root, author="human")
    try:
        module.append_entry(kb_root, "valid-operation", "## Entry")
    except ValueError as exc:
        assert "kb-bot-authored" in str(exc)
    else:
        raise AssertionError("expected ownership rejection")

    make_log(kb_root)
    try:
        module.append_entry(kb_root, "bad id with spaces", "## Entry")
    except ValueError as exc:
        assert "operation id" in str(exc)
    else:
        raise AssertionError("expected operation-id rejection")
