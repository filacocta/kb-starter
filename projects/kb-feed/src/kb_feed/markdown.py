from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from kb_feed.errors import KbWriteRejected


FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
LEVEL_2_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")


def assert_kb_bot_authored(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise KbWriteRejected(f"{path} has no YAML frontmatter.")
    if not re.search(r"^author:\s*kb-bot\s*$", match.group("body"), re.MULTILINE):
        raise KbWriteRejected(f"{path} is not authored by kb-bot.")


def append_topic_reading_list(path: Path, line: str, today: date | None = None) -> bool:
    assert_kb_bot_authored(path)
    text = path.read_text(encoding="utf-8")
    if _has_exact_line(text, line):
        return False

    lines = text.splitlines()
    heading_index = _find_topic_queue_heading(lines)
    if heading_index is None:
        heading_index = _insert_topic_queue_heading(lines)

    _insert_under_heading(lines, heading_index, line)
    updated = "\n".join(lines) + "\n"
    updated = update_modified_date(updated, today=today)
    _atomic_write_text(path, updated)
    return True


def append_topic_auto_approved(path: Path, line: str, today: date | None = None) -> bool:
    assert_kb_bot_authored(path)
    text = path.read_text(encoding="utf-8")
    if _has_exact_line(text, line):
        return False

    lines = text.splitlines()
    heading_index = _find_level_2_heading(lines, "auto-approved")
    if heading_index is None:
        heading_index = _insert_auto_approved_heading(lines)

    _insert_under_heading(lines, heading_index, line)
    updated = "\n".join(lines) + "\n"
    updated = update_modified_date(updated, today=today)
    _atomic_write_text(path, updated)
    return True


def append_dive_queue(path: Path, topic_slug: str, line: str, today: date | None = None) -> bool:
    return _append_under_heading(path, f"## {topic_slug}", line, today=today)


def append_review_queue(path: Path, line: str, today: date | None = None) -> bool:
    return _append_to_end(path, line, today=today)


def telegram_annotation(thread_label: str, captured_on: date) -> str:
    return f"(via Telegram #{thread_label}, {captured_on.isoformat()})"


def _append_under_heading(path: Path, heading: str, line: str, today: date | None = None) -> bool:
    assert_kb_bot_authored(path)
    text = path.read_text(encoding="utf-8")
    if _has_exact_line(text, line):
        return False

    lines = text.splitlines()
    try:
        heading_index = lines.index(heading)
    except ValueError as exc:
        raise KbWriteRejected(f"{path} is missing required heading {heading!r}.") from exc

    _insert_under_heading(lines, heading_index, line)
    updated = "\n".join(lines) + "\n"
    updated = update_modified_date(updated, today=today)
    _atomic_write_text(path, updated)
    return True


def _append_to_end(path: Path, line: str, today: date | None = None) -> bool:
    assert_kb_bot_authored(path)
    text = path.read_text(encoding="utf-8")
    if _has_exact_line(text, line):
        return False
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    lines.append(line)
    updated = "\n".join(lines) + "\n"
    updated = update_modified_date(updated, today=today)
    _atomic_write_text(path, updated)
    return True


def _has_exact_line(text: str, line: str) -> bool:
    return line in text.splitlines()


def _find_topic_queue_heading(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        title = level_2_heading_title(line)
        if title is None:
            continue
        normalized = title.lower()
        if "queue" in normalized or "pending" in normalized:
            return index
    return None


def _insert_topic_queue_heading(lines: list[str]) -> int:
    insert_at = _topic_queue_fallback_index(lines)
    block: list[str] = []
    if insert_at > 0 and lines[insert_at - 1] != "":
        block.append("")
    heading_index = insert_at + len(block)
    block.extend(["## Queue", ""])
    lines[insert_at:insert_at] = block
    return heading_index


def _insert_auto_approved_heading(lines: list[str]) -> int:
    queue_index = _find_topic_queue_heading(lines)
    insert_at = queue_index if queue_index is not None else _topic_queue_fallback_index(lines)
    block: list[str] = []
    if insert_at > 0 and lines[insert_at - 1] != "":
        block.append("")
    heading_index = insert_at + len(block)
    block.extend(["## Auto-approved", ""])
    if insert_at < len(lines) and lines[insert_at] != "":
        block.append("")
    lines[insert_at:insert_at] = block
    return heading_index


def _topic_queue_fallback_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        title = level_2_heading_title(line)
        if title is None:
            continue
        normalized = title.lower()
        if normalized.startswith("processed") or normalized.startswith("failed"):
            return index
    for index, line in enumerate(lines):
        if level_2_heading_title(line) is not None:
            return index
    return len(lines)


def _insert_under_heading(lines: list[str], heading_index: int, line: str) -> None:
    insert_at = len(lines)
    heading_level = heading_level_of(lines[heading_index])
    for index in range(heading_index + 1, len(lines)):
        if heading_level_of(lines[index]) <= heading_level:
            insert_at = index
            break

    while insert_at > heading_index + 1 and lines[insert_at - 1] == "":
        insert_at -= 1

    lines.insert(insert_at, line)


def level_2_heading_title(line: str) -> str | None:
    match = LEVEL_2_HEADING_RE.match(line)
    return match.group("title") if match else None


def _find_level_2_heading(lines: list[str], title: str) -> int | None:
    normalized_title = title.strip().lower()
    for index, line in enumerate(lines):
        found = level_2_heading_title(line)
        if found is not None and found.strip().lower() == normalized_title:
            return index
    return None


def heading_level_of(line: str) -> int:
    match = re.match(r"^(#+)\s", line)
    return len(match.group(1)) if match else 99


def update_modified_date(text: str, today: date | None = None) -> str:
    value = (today or date.today()).isoformat()
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise KbWriteRejected("Markdown file has no YAML frontmatter.")
    start, end = match.span("body")
    body = match.group("body")
    if re.search(r"^modified:\s*.*$", body, re.MULTILINE):
        body = re.sub(r"^modified:\s*.*$", f"modified: {value}", body, count=1, flags=re.MULTILINE)
    else:
        body = f"{body}\nmodified: {value}"
    return f"{text[:start]}{body}{text[end:]}"


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
