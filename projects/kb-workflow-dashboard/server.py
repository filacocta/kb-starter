#!/usr/bin/env python3
"""Read-only local dashboard for the user's KB workflow."""
from __future__ import annotations

import argparse
import html
import ipaddress
import json
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_KB_ROOT = Path.home() / "Vault" / "knowledgebase"
DEFAULT_STATE_DB = Path.home() / ".local" / "state" / "kb-feed" / "state.sqlite"
# Per-project operational-health metric files (<project>.json) written by external jobs
# (e.g. the sportsbook dismissal review). Surfaced on the Project Health page.
DEFAULT_PROJECT_HEALTH_DIR = Path.home() / ".local" / "state" / "project-health"
_HEALTH_RANK = {"red": 3, "yellow": 2, "green": 1, "": 0}

# cswap output parsing
_ACCOUNT_RE = re.compile(r"^\s+(\d+):\s+(\S+)\s+\[([^\]]+)\](\s+\(active\))?")
_QUOTA_RE   = re.compile(r"^\s+[├└]\s+(5h|7d):\s+(\d+)%\s+resets\s+(.+?)\s{2,}in\s+(.+)")
_OAUTH_RE   = re.compile(r"^\s+[•]\s+oauth:\s+(.+)")
_SESSION_RE = re.compile(r"^\s+[●○]\s+(\S+)\s+(.+?)\s+\((\d+)\s+session")
_UPGRADE_RE = re.compile(r"newer version.*available.*\((\S+)\)")
REQUIRED_KB_PATHS = (
    "_schema.md",
    "_rules.md",
    "_index.md",
    "_checkpoint.md",
    "_log.md",
    "_dive-queue.md",
    "readinglist/Links.md",
    "readinglist/topics.md",
    "ideas/_backlog.md",
    "topics",
    "projects",
)
CHECKBOX_RE = re.compile(r"^-\s+\[([ xX!~?])\]\s*")
CHECKBOX_STATUS = {
    " ": "unchecked",
    "x": "checked",
    "!": "failed",
    "~": "active",
    "?": "review",
}
# Canonical reading-list section ladder (see _schema.md / _rules.md). A queue's
# `## <name>` headings are matched against this list; any `### sub-group` rolls
# up into the canonical `##` parent it sits under so ladder counts stay accurate
# even when items are filed under dated sub-headings (e.g. field-sensing).
CANONICAL_LADDER = ["Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry"]
LADDER_OTHER = "Other"


@dataclass
class QueueFile:
    name: str
    path: str
    unchecked: int
    checked: int
    failed: int
    active: int
    review: int
    kind: str
    sections: list[dict[str, object]]
    items: list[dict[str, object]]
    ladder: dict[str, dict[str, int]]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip()
    return text


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def checkbox_counts(lines: list[str]) -> dict[str, int]:
    counts = empty_counts()
    for line in lines:
        match = CHECKBOX_RE.match(line.lstrip())
        if not match:
            continue
        status = CHECKBOX_STATUS[match.group(1).lower()]
        counts[status] += 1
    return counts


def empty_counts() -> dict[str, int]:
    return {"unchecked": 0, "checked": 0, "failed": 0, "active": 0, "review": 0}


def iter_visible_lines(text: str) -> list[tuple[int, str]]:
    """Return non-frontmatter, non-HTML-comment lines with original line numbers."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                start = index + 1
                break

    visible: list[tuple[int, str]] = []
    in_comment = False
    for line_number, line in enumerate(lines[start:], start=start + 1):
        cursor = 0
        output = ""
        while cursor < len(line):
            if in_comment:
                end = line.find("-->", cursor)
                if end == -1:
                    cursor = len(line)
                    continue
                in_comment = False
                cursor = end + 3
                continue
            start_comment = line.find("<!--", cursor)
            if start_comment == -1:
                output += line[cursor:]
                break
            output += line[cursor:start_comment]
            end = line.find("-->", start_comment + 4)
            if end == -1:
                in_comment = True
                break
            cursor = end + 3
        visible.append((line_number, output))
    return visible


def parse_queue_file(path: Path, kb_root: Path, label: str | None = None, kind: str = "queue") -> QueueFile:
    visible_lines = iter_visible_lines(read_text(path))
    sections: list[dict[str, object]] = []
    items: list[dict[str, object]] = []
    ladder: dict[str, dict[str, int]] = {name: empty_counts() for name in CANONICAL_LADDER}
    ladder[LADDER_OTHER] = empty_counts()
    current = {"heading": "Top", "unchecked": 0, "checked": 0, "failed": 0, "active": 0, "review": 0}
    current_heading = "Top"
    current_ladder = LADDER_OTHER  # canonical bucket the current line rolls up into
    has_seen_content = False
    for line_number, line in visible_lines:
        heading = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if heading:
            if has_seen_content or any(current[k] for k in ("unchecked", "checked", "failed", "active", "review")):
                sections.append(current)
            level = len(heading.group(1))
            current_heading = heading.group(2)
            # `##` sets the canonical bucket; `###` keeps the parent `##` bucket.
            if level == 2:
                current_ladder = current_heading if current_heading in ladder else LADDER_OTHER
            current = {"heading": current_heading, "unchecked": 0, "checked": 0, "failed": 0, "active": 0, "review": 0}
            has_seen_content = True
            continue

        stripped = line.lstrip()
        status = ""
        item_match = CHECKBOX_RE.match(stripped)
        if item_match:
            status = CHECKBOX_STATUS[item_match.group(1).lower()]
            current[status] += 1
            ladder[current_ladder][status] += 1

        if status:
            items.append(
                {
                    "status": status,
                    "section": current_heading,
                    "ladder": current_ladder,
                    "line": line_number,
                    "text": CHECKBOX_RE.sub("", stripped, count=1).strip(),
                }
            )

    if has_seen_content or any(current[k] for k in ("unchecked", "checked", "failed", "active", "review")):
        sections.append(current)
    counts = checkbox_counts([line for _, line in visible_lines])
    return QueueFile(
        name=label or path.parent.name,
        path=relpath(path, kb_root),
        unchecked=counts["unchecked"],
        checked=counts["checked"],
        failed=counts["failed"],
        active=counts["active"],
        review=counts["review"],
        kind=kind,
        sections=sections,
        items=items,
        ladder=ladder,
    )


def attention_items(queue: QueueFile) -> list[dict[str, object]]:
    return [item for item in queue.items if item["status"] in {"unchecked", "failed", "active", "review"}]


def queue_payload(queue: QueueFile, include_items: bool = False, preview_limit: int = 3) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": queue.name,
        "path": queue.path,
        "unchecked": queue.unchecked,
        "checked": queue.checked,
        "failed": queue.failed,
        "active": queue.active,
        "review": queue.review,
        "kind": queue.kind,
        "sections": queue.sections,
        "ladder": queue.ladder,
        "item_count": len(queue.items),
    }
    if include_items:
        payload["items"] = queue.items
    else:
        payload["preview_items"] = attention_items(queue)[:preview_limit]
    return payload


def parse_checkpoint(kb_root: Path) -> dict[str, object]:
    path = kb_root / "_checkpoint.md"
    text = read_text(path)
    body = strip_frontmatter(text)
    status = "unknown"
    operation = "unknown"
    last_updated = ""
    for line in body.splitlines():
        if line.startswith("**Operation:**"):
            operation = line.split("**Operation:**", 1)[1].strip()
        elif line.startswith("**Status:**"):
            status = line.split("**Status:**", 1)[1].strip()
        elif line.startswith("**Last updated:**"):
            last_updated = line.split("**Last updated:**", 1)[1].strip()
    counts = checkbox_counts(body.splitlines())
    return {
        "path": relpath(path, kb_root),
        "operation": operation,
        "status": status,
        "last_updated": last_updated,
        "counts": counts,
        "is_active": status in {"in-progress", "resumable"},
    }


def parse_topics(kb_root: Path) -> list[dict[str, object]]:
    topics_dir = kb_root / "topics"
    rows: list[dict[str, object]] = []
    if not topics_dir.exists():
        return rows
    for topic_dir in sorted(p for p in topics_dir.iterdir() if p.is_dir()):
        summary = parse_frontmatter(read_text(topic_dir / "_summary.md"))
        reading_list = topic_dir / "reading-list.md"
        queue = parse_queue_file(reading_list, kb_root) if reading_list.exists() else None
        sources = len(list((topic_dir / "sources").glob("*.md"))) if (topic_dir / "sources").exists() else 0
        notes = len(list((topic_dir / "notes").rglob("*.md"))) if (topic_dir / "notes").exists() else 0
        rows.append(
            {
                "slug": topic_dir.name,
                "title": summary.get("title", topic_dir.name),
                "status": summary.get("status", ""),
                "modified": summary.get("modified", ""),
                "sources": sources,
                "notes": notes,
                "queue": queue_payload(queue) if queue else None,
            }
        )
    return rows


def parse_projects(kb_root: Path) -> list[dict[str, object]]:
    projects_dir = kb_root / "projects"
    rows: list[dict[str, object]] = []
    if not projects_dir.exists():
        return rows
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        summary = parse_frontmatter(read_text(project_dir / "_summary.md"))
        todos = project_dir / "_todos.md"
        todo_counts = checkbox_counts(strip_frontmatter(read_text(todos)).splitlines()) if todos.exists() else empty_counts()
        rows.append(
            {
                "slug": project_dir.name,
                "title": summary.get("title", project_dir.name),
                "status": summary.get("status", ""),
                "modified": summary.get("modified", ""),
                "todo_counts": todo_counts,
            }
        )
    return rows


def queue_specs(kb_root: Path) -> dict[str, tuple[Path, str, str]]:
    specs: list[tuple[Path, str, str]] = [
        (kb_root / "readinglist" / "Links.md", "URL Inbox", "queue"),
        (kb_root / "readinglist" / "topics.md", "Topic Interests", "queue"),
        (kb_root / "ideas" / "_backlog.md", "Ideas Backlog", "parking-lot"),
        (kb_root / "_dive-queue.md", "Research Dive Queue", "generated"),
    ]
    topics_dir = kb_root / "topics"
    if topics_dir.exists():
        for topic_dir in sorted(p for p in topics_dir.iterdir() if p.is_dir()):
            reading_list = topic_dir / "reading-list.md"
            if reading_list.exists():
                specs.append((reading_list, topic_dir.name, "queue"))
    return {relpath(path, kb_root): (path, label, kind) for path, label, kind in specs}


def parse_queue_by_relpath(kb_root: Path, queue_path: str) -> QueueFile:
    if not queue_path or Path(queue_path).is_absolute() or ".." in Path(queue_path).parts:
        raise KeyError(queue_path)
    specs = queue_specs(kb_root)
    if queue_path not in specs:
        raise KeyError(queue_path)
    path, label, kind = specs[queue_path]
    return parse_queue_file(path, kb_root, label=label, kind=kind)


def parse_dive_queue(kb_root: Path) -> QueueFile:
    return parse_queue_file(kb_root / "_dive-queue.md", kb_root, label="Research Dive Queue", kind="generated")


def parse_recent_log(kb_root: Path, limit: int = 5) -> list[dict[str, str]]:
    text = strip_frontmatter(read_text(kb_root / "_log.md"))
    entries = re.split(r"\n(?=## \[\d{4}-\d{2}-\d{2}\])", text)
    out: list[dict[str, object]] = []
    for order, entry in enumerate(entries):
        if not entry.startswith("## ["):
            continue
        lines = entry.strip().splitlines()
        title = lines[0].replace("## ", "", 1)
        summary = " ".join(line.strip() for line in lines[1:4] if line.strip())
        date_match = re.match(r"\[(\d{4}-\d{2}-\d{2})\]", title)
        out.append({"title": title, "summary": summary, "date": date_match.group(1) if date_match else "", "order": order})
    out.sort(key=lambda item: (str(item["date"]), int(item["order"])), reverse=True)
    return [{"title": str(item["title"]), "summary": str(item["summary"])} for item in out[:limit]]


TELEGRAM_PROV_RE = re.compile(r"\(via Telegram #?([\w./-]+)", re.IGNORECASE)
AGENT_PROV_RE = re.compile(r"\(via agent ([\w.-]+)", re.IGNORECASE)


def classify_provenance(text: str) -> tuple[str, str | None]:
    """Classify a queue item's capture source from its provenance tag."""
    m = TELEGRAM_PROV_RE.search(text)
    if m:
        return "telegram", m.group(1).rstrip(",.").lower()
    m = AGENT_PROV_RE.search(text)
    if m:
        return "agent", m.group(1).rstrip(",.").lower()
    return "manual", None


def parse_provenance(kb_root: Path) -> dict[str, object]:
    """Aggregate captured items by source (Telegram vs agent vs manual) across
    the global inbox and every topic reading-list. Tags are written at capture
    time (Rule 31 / kb-capture-url provenance)."""
    paths = [kb_root / "readinglist" / "Links.md"]
    topics_dir = kb_root / "topics"
    if topics_dir.exists():
        for d in sorted(p for p in topics_dir.iterdir() if p.is_dir()):
            rl = d / "reading-list.md"
            if rl.exists():
                paths.append(rl)

    def bucket() -> dict[str, int]:
        return {"total": 0, "open": 0}

    by_source = {"telegram": bucket(), "agent": bucket(), "manual": bucket()}
    threads: dict[str, dict[str, int]] = {}
    agents: dict[str, dict[str, int]] = {}
    total = 0
    open_total = 0
    for path in paths:
        for item in parse_queue_file(path, kb_root).items:
            src, detail = classify_provenance(str(item["text"]))
            is_open = item["status"] == "unchecked"
            total += 1
            open_total += 1 if is_open else 0
            by_source[src]["total"] += 1
            by_source[src]["open"] += 1 if is_open else 0
            sub = threads if src == "telegram" else agents if src == "agent" else None
            if sub is not None and detail:
                row = sub.setdefault(detail, bucket())
                row["total"] += 1
                row["open"] += 1 if is_open else 0
    rank = lambda d: dict(sorted(d.items(), key=lambda kv: (-kv[1]["total"], kv[0])))
    # The capture-topic marker decides where the WebFetch capture hook routes
    # agent web-fetches: a topic's ## Pending when set, else the global inbox.
    marker = Path.home() / ".local" / "state" / "kb-feed" / "capture-topic"
    try:
        capture_topic = marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        capture_topic = None
    return {
        "total": total,
        "open": open_total,
        "by_source": by_source,
        "telegram_threads": rank(threads),
        "agents": rank(agents),
        "capture_topic": capture_topic,
    }


def parse_kb_feed_state(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {"available": False, "path": str(db_path), "error": "state DB not found"}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        with con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            out: dict[str, object] = {"available": True, "path": str(db_path), "tables": sorted(tables)}
            if "outbox" in tables:
                out["outbox"] = [
                    dict(row)
                    for row in con.execute(
                        "SELECT status, COUNT(*) AS count FROM outbox GROUP BY status ORDER BY status"
                    )
                ]
            if "capture_outbox" in tables:
                out["capture_outbox"] = [
                    dict(row)
                    for row in con.execute(
                        "SELECT status, COUNT(*) AS count FROM capture_outbox GROUP BY status ORDER BY status"
                    )
                ]
            if "runtime_meta" in tables:
                out["runtime_meta"] = {
                    row["key"]: row["value"]
                    for row in con.execute(
                        "SELECT key, value FROM runtime_meta WHERE key IN ('last_activity_at', 'last_kb_commit_at', 'last_link_triage_at', 'last_inspiration_triage_at')"
                    )
                }
            return out
    except Exception as exc:
        return {"available": False, "path": str(db_path), "error": str(exc)}


def validate_kb_root(kb_root: Path) -> list[str]:
    missing: list[str] = []
    for required in REQUIRED_KB_PATHS:
        if not (kb_root / required).exists():
            missing.append(required)
    return missing


_lint_cache: dict = {"ts": 0.0, "data": {}}
LINT_CACHE_TTL = 60  # seconds


def parse_lint_output(text: str, kb_root: Path) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    summary: dict = {"kb_root": str(kb_root)}
    section = None
    current_item: dict | None = None

    for line in text.splitlines():
        if line.startswith("## Errors"):
            section = "errors"
            current_item = None
            continue
        if line.startswith("## Warnings"):
            section = "warnings"
            current_item = None
            continue
        if line.startswith("## Summary"):
            section = "summary"
            current_item = None
            continue
        if line.startswith("## "):
            section = None
            current_item = None
            continue

        if section == "summary":
            m = re.match(r"-\s+(\d+) md files scanned", line)
            if m:
                summary["files_scanned"] = int(m.group(1))
            m = re.match(r"-\s+(\d+) errors,\s+(\d+) warnings", line)
            if m:
                summary["errors"] = int(m.group(1))
                summary["warnings"] = int(m.group(2))
            continue

        if section in ("errors", "warnings"):
            if line.startswith("### ") or line.strip().startswith("…and"):
                current_item = None
                continue
            ctx_m = re.match(r"^\s+↳\s+`(.+)`\s*$", line)
            if ctx_m and current_item is not None:
                current_item["context"] = ctx_m.group(1)
                continue
            item_m = re.match(r"^\s*-\s+`([^`]+):(\d+)`\s+—\s+(.+)$", line)
            if item_m:
                current_item = {
                    "file": item_m.group(1),
                    "line": int(item_m.group(2)),
                    "message": item_m.group(3).strip(),
                    "context": "",
                }
                (errors if section == "errors" else warnings).append(current_item)
                continue
            item_m2 = re.match(r"^\s*-\s+`([^`]+)`\s+—\s+(.+)$", line)
            if item_m2:
                current_item = {
                    "file": item_m2.group(1),
                    "line": None,
                    "message": item_m2.group(2).strip(),
                    "context": "",
                }
                (errors if section == "errors" else warnings).append(current_item)
                continue
            current_item = None

    for e in errors:
        msg = e["message"]
        if "unresolved wikilink:" in msg:
            e["code"] = "unresolved_wikilink"
            e["target"] = msg.split("unresolved wikilink:", 1)[1].strip()
        else:
            e["code"] = "error"
            e["target"] = ""
    for w in warnings:
        w["code"] = "warning"
        w["target"] = ""

    summary.setdefault("errors", len(errors))
    summary.setdefault("warnings", len(warnings))
    return {"errors": errors, "warnings": warnings, "summary": summary}


def run_lint(kb_root: Path, bust: bool = False) -> dict:
    now = time.monotonic()
    if not bust and _lint_cache["ts"] and (now - _lint_cache["ts"]) < LINT_CACHE_TTL:
        return _lint_cache["data"]
    try:
        proc = subprocess.run(
            ["kb-lint", "--no-orphans"],
            capture_output=True, text=True, timeout=120,
        )
        data = parse_lint_output(proc.stdout, kb_root)
    except Exception as exc:
        data = {"errors": [], "warnings": [], "summary": {"error": str(exc), "kb_root": str(kb_root)}}
    _lint_cache["ts"] = now
    _lint_cache["data"] = data
    return data


def host_is_loopback(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def build_status(kb_root: Path, state_db: Path) -> dict[str, object]:
    links = parse_queue_file(kb_root / "readinglist" / "Links.md", kb_root, label="URL Inbox")
    topics_inbox = parse_queue_file(kb_root / "readinglist" / "topics.md", kb_root, label="Topic Interests")
    ideas = parse_queue_file(kb_root / "ideas" / "_backlog.md", kb_root, label="Ideas Backlog", kind="parking-lot")
    topics = parse_topics(kb_root)
    projects = parse_projects(kb_root)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_root": str(kb_root),
        "checkpoint": parse_checkpoint(kb_root),
        "global_queues": [queue_payload(links), queue_payload(topics_inbox)],
        "ideas_backlog": queue_payload(ideas),
        "dive_queue": queue_payload(parse_dive_queue(kb_root)),
        "topics": topics,
        "projects": projects,
        "kb_feed": parse_kb_feed_state(state_db),
        "kb_feed_timers": parse_kb_feed_timers(),
        "provenance": parse_provenance(kb_root),
        "recent_log": parse_recent_log(kb_root),
    }


def append_audit(app_dir: Path, op: str, entry: dict) -> None:
    """Append one JSON line to the dashboard-local execution log. Best-effort."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "op": op, **entry}
    try:
        with open(app_dir / "command-log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # pragma: no cover - logging must never break the request
        pass


# kb-feed scheduled timers the dashboard can read and (for installed ones) toggle.
KB_FEED_TIMERS = [
    ("kb-feed-digest-weekly.timer", "Weekly digest", "Mon 10:00 — Claude reviews the queues and DMs proposals."),
    ("kb-feed-digest-reminder.timer", "Digest reminder", "Every 15 min — resends a snoozed digest when due."),
    ("kb-feed-nudge.timer", "Triage nudge", "Wed & Sat 09:00 — DMs a count of links/notes/proposals awaiting triage."),
]
TIMER_TOGGLE_ALLOW = {unit for unit, _, _ in KB_FEED_TIMERS}
# One-time install for the nudge units (templates ship in the kb-feed repo but
# were never copied into ~/.config/systemd/user/). Surfaced as a copyable
# handoff in the UI — the dashboard does not copy unit files itself.
NUDGE_INSTALL_CMD = (
    "cp ~/Vault/projects/kb-feed/deploy/systemd/kb-feed-nudge.{service,timer} ~/.config/systemd/user/\n"
    "systemctl --user daemon-reload\n"
    "systemctl --user enable --now kb-feed-nudge.timer"
)


def parse_kb_feed_timers() -> list[dict[str, object]]:
    """Read state of the kb-feed schedules via `systemctl --user show` (read-only).
    systemctl renders the *USec properties as human-readable strings; use them
    as-is (realtime for OnCalendar timers, monotonic 'relative' for OnUnitActive)."""
    props_names = ["ActiveState", "UnitFileState", "LoadState",
                   "NextElapseUSecRealtime", "NextElapseUSecMonotonic", "LastTriggerUSec"]
    out: list[dict[str, object]] = []
    for unit, label, desc in KB_FEED_TIMERS:
        entry: dict[str, object] = {"unit": unit, "label": label, "desc": desc}
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "show", unit, "--no-pager",
                 *[f"--property={p}" for p in props_names]],
                capture_output=True, text=True, timeout=5,
            )
            props = dict(
                line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
            )
        except Exception:
            out.append({**entry, "available": False})
            continue
        installed = props.get("LoadState", "") not in ("not-found", "")
        next_rt = props.get("NextElapseUSecRealtime", "").strip()
        next_mono = props.get("NextElapseUSecMonotonic", "").strip()
        next_val = next_rt or (next_mono if next_mono and next_mono != "0" else "")
        if next_val in ("infinity", "0"):
            next_val = ""
        last = props.get("LastTriggerUSec", "").strip()
        if not installed:
            next_val = last = ""
        entry.update({
            "available": True,
            "installed": installed,
            "active": props.get("ActiveState") == "active",
            "enabled": props.get("UnitFileState", ""),
            "next": next_val or None,
            "last": last or None,
            "install_cmd": NUDGE_INSTALL_CMD if (not installed and "nudge" in unit) else None,
        })
        out.append(entry)
    return out


def run_timer_toggle(app_dir: Path, payload: dict, action: str) -> tuple[int, dict]:
    """Enable/disable a kb-feed timer via `systemctl --user` (allowlisted, no shell, audited)."""
    unit = payload.get("unit", "")
    if action not in ("enable", "disable"):
        return 400, {"ok": False, "error": "bad_action", "message": action}
    if unit not in TIMER_TOGGLE_ALLOW:
        return 400, {"ok": False, "error": "unit_not_allowed", "message": str(unit)}
    try:
        proc = subprocess.run(
            ["systemctl", "--user", action, "--now", unit],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        return 500, {"ok": False, "error": "exec_failed", "message": str(exc)}
    ok = proc.returncode == 0
    msg = (proc.stderr or proc.stdout or ("done" if ok else "failed")).strip()
    result = {"ok": ok, "action": action, "unit": unit, "message": msg}
    if ok:
        append_audit(app_dir, "timer", {"unit": unit, "action": action, "message": msg})
    return (200 if ok else 422), result


def run_promote(kb_root: Path, app_dir: Path, payload: dict, execute: bool) -> tuple[int, dict]:
    """Validate a promote request and shell out to `_tools/kb-promote.py` with a
    fixed arg list (never a shell). This is a *promote* endpoint, not a generic
    runner: `file` must be an allowlisted topic reading-list, and `from`/`to`
    must be canonical ladder sections."""
    file = payload.get("file", "")
    url = payload.get("url", "")
    frm = payload.get("from", "")
    to = payload.get("to", "")

    if not isinstance(file, str) or ".." in Path(file).parts or not (
        file.startswith("topics/") and file.endswith("reading-list.md")
    ):
        return 400, {"ok": False, "error": "invalid_file", "message": "file must be a topic reading-list path"}
    if file not in queue_specs(kb_root):
        return 404, {"ok": False, "error": "unknown_queue", "message": file}
    if frm not in CANONICAL_LADDER or to not in CANONICAL_LADDER:
        return 400, {"ok": False, "error": "invalid_section", "message": "from/to must be canonical ladder sections"}
    if frm == to:
        return 400, {"ok": False, "error": "same_section", "message": "from and to are identical"}
    if not isinstance(url, str) or not url.strip():
        return 400, {"ok": False, "error": "missing_url", "message": "url required"}

    script = kb_root / "_tools" / "kb-promote.py"
    if not script.exists():
        return 500, {"ok": False, "error": "missing_tool", "message": "kb-promote.py not found"}

    args = [sys.executable, str(script), "--kb-root", str(kb_root),
            "--file", file, "--url", url, "--from", frm, "--to", to, "--json"]
    if not execute:
        args.append("--dry-run")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return 500, {"ok": False, "error": "exec_failed", "message": str(exc)}

    try:
        result = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        result = {"ok": False, "message": (proc.stderr or proc.stdout or "no output").strip()}
    result["executed"] = bool(execute and result.get("ok"))
    if execute and result.get("ok"):
        append_audit(app_dir, "promote", {"file": file, "url": url, "from": frm, "to": to,
                                          "line": result.get("line"), "message": result.get("message")})
    return (200 if result.get("ok") else 422), result


def parse_cswap_usage() -> dict:
    """Run `cswap --list --token-status` and parse usage for all accounts + sessions."""
    # cswap lives in ~/.local/bin which may not be on systemd's PATH
    cswap_candidates = [
        "cswap",
        str(Path.home() / ".local" / "bin" / "cswap"),
    ]
    for cmd in cswap_candidates:
        try:
            proc = subprocess.run(
                [cmd, "--list", "--token-status"],
                capture_output=True, text=True, timeout=10,
            )
            return _parse_cswap_output(proc.stdout)
        except FileNotFoundError:
            continue
    return {"error": "cswap not found", "accounts": [], "sessions": [], "upgrade_available": None}


def _parse_cswap_output(text: str) -> dict:
    accounts: list[dict] = []
    sessions: list[dict] = []
    current: dict | None = None
    in_sessions = False
    upgrade_available: str | None = None

    for line in text.splitlines():
        m = _UPGRADE_RE.search(line)
        if m:
            upgrade_available = m.group(1)
            continue

        if "Running instances:" in line:
            in_sessions = True
            if current:
                accounts.append(current)
                current = None
            continue

        if in_sessions:
            m = _SESSION_RE.match(line)
            if m:
                sessions.append({
                    "type": m.group(1),
                    "path": m.group(2).strip(),
                    "count": int(m.group(3)),
                })
            continue

        m = _ACCOUNT_RE.match(line)
        if m:
            if current:
                accounts.append(current)
            current = {
                "slot": int(m.group(1)),
                "email": m.group(2),
                "org": m.group(3),
                "active": bool(m.group(4)),
                "quota_5h": None,
                "quota_7d": None,
                "oauth": None,
            }
            continue

        if current is None:
            continue

        m = _QUOTA_RE.match(line)
        if m:
            key = "quota_5h" if m.group(1) == "5h" else "quota_7d"
            current[key] = {
                "pct": int(m.group(2)),
                "resets_at": m.group(3).strip(),
                "resets_in": m.group(4).strip(),
            }
            continue

        m = _OAUTH_RE.match(line)
        if m:
            current["oauth"] = m.group(1).strip()

    if current:
        accounts.append(current)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "accounts": accounts,
        "sessions": sessions,
        "upgrade_available": upgrade_available,
    }


def parse_project_health(health_dir: Path) -> dict:
    """Aggregate per-project operational-health metric files for the Project Health page.

    Each `<project>.json` is written by an external job and shaped:
        {project, title, updated, metrics: [{name, status, summary, detail, updated}]}
    status ∈ green | yellow | red. A metric whose `updated` is > STALE_HOURS old is marked stale
    (the producer stopped running) and rolls up as yellow. Returns the projects (worst-status
    first) plus a roll-up summary."""
    stale_hours = 36
    now = datetime.now(timezone.utc)
    projects: list[dict] = []
    summary = {"green": 0, "yellow": 0, "red": 0, "stale": 0}

    def _stale(updated: object) -> bool:
        try:
            t = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            return (now - t).total_seconds() > stale_hours * 3600
        except Exception:
            return False

    if health_dir.exists():
        for f in sorted(health_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            worst = "green"
            for m in data.get("metrics", []):
                st = (m.get("status") or "green").lower()
                m["stale"] = _stale(m.get("updated") or data.get("updated"))
                if m["stale"]:
                    summary["stale"] += 1
                    st = "yellow" if st == "green" else st
                    m["status"] = st
                summary[st] = summary.get(st, 0) + 1
                if _HEALTH_RANK.get(st, 0) > _HEALTH_RANK.get(worst, 0):
                    worst = st
            data["worst"] = worst
            projects.append(data)
    projects.sort(key=lambda d: -_HEALTH_RANK.get(d.get("worst", ""), 0))
    return {"generated": now.isoformat(), "projects": projects,
            "summary": summary, "health_dir": str(health_dir)}


class DashboardHandler(SimpleHTTPRequestHandler):
    kb_root: Path
    state_db: Path
    static_dir: Path
    project_health_dir: Path = DEFAULT_PROJECT_HEALTH_DIR

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.static_dir), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            missing = validate_kb_root(self.kb_root)
            if missing:
                self.send_json(
                    {"error": "invalid_kb_root", "kb_root": str(self.kb_root), "missing": missing},
                    status=500,
                )
                return
            self.send_json(build_status(self.kb_root, self.state_db))
            return
        if parsed.path == "/api/queue":
            queue_path = parse_qs(parsed.query).get("path", [""])[0]
            if not queue_path:
                self.send_json({"error": "missing_path"}, status=400)
                return
            try:
                queue = parse_queue_by_relpath(self.kb_root, queue_path)
            except KeyError:
                self.send_json({"error": "unknown_queue", "path": queue_path}, status=404)
                return
            self.send_json(queue_payload(queue, include_items=True))
            return
        if parsed.path == "/api/lint":
            bust = "bust" in parse_qs(parsed.query)
            self.send_json(run_lint(self.kb_root, bust=bool(bust)))
            return
        if parsed.path == "/api/project-health":
            self.send_json(parse_project_health(self.project_health_dir))
            return
        if parsed.path == "/api/claude-usage":
            self.send_json(parse_cswap_usage())
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/api/promote/preview", "/api/promote/execute"):
            if not self._origin_is_local():
                self.send_json({"ok": False, "error": "forbidden", "message": "non-local origin"}, status=403)
                return
            payload = self._read_json_body()
            if payload is None:
                self.send_json({"ok": False, "error": "bad_request", "message": "invalid JSON body"}, status=400)
                return
            execute = parsed.path.endswith("execute")
            app_dir = self.static_dir.parent
            status, result = run_promote(self.kb_root, app_dir, payload, execute)
            self.send_json(result, status=status)
            return
        if parsed.path in ("/api/timer/enable", "/api/timer/disable"):
            if not self._origin_is_local():
                self.send_json({"ok": False, "error": "forbidden", "message": "non-local origin"}, status=403)
                return
            payload = self._read_json_body()
            if payload is None:
                self.send_json({"ok": False, "error": "bad_request", "message": "invalid JSON body"}, status=400)
                return
            action = "enable" if parsed.path.endswith("enable") else "disable"
            status, result = run_timer_toggle(self.static_dir.parent, payload, action)
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/probe":
            payload = self._read_json_body()
            if payload is None or not isinstance(payload.get("urls"), list):
                self.send_json({"ok": False, "error": "bad_request"}, status=400)
                return
            results = {}
            for url in payload["urls"][:32]:
                try:
                    p = urlparse(url)
                    host = p.hostname or ""
                    port = p.port or (443 if p.scheme == "https" else 80)
                    # Resolve hostname then attempt TCP connect (3s timeout)
                    addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4]
                    sock = socket.socket()
                    sock.settimeout(3)
                    sock.connect(addr)
                    sock.close()
                    results[url] = True
                except Exception:
                    results[url] = False
            self.send_json({"ok": True, "results": results})
            return
        self.send_json({"error": "not_found"}, status=404)

    def _origin_is_local(self) -> bool:
        """Reject writes driven from a non-loopback page (CSRF guard for the
        local tool). The server already binds loopback; this also checks Origin
        and Host so a random local web page cannot POST to us."""
        origin = self.headers.get("Origin")
        if origin:
            host = urlparse(origin).hostname or ""
            if not host_is_loopback(host):
                return False
        host_hdr = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host_is_loopback(host_hdr) if host_hdr else True

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0 or length > 64 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def send_json(self, data: object, status: int = 200) -> None:
        encoded = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def list_directory(self, path: str):  # type: ignore[override]
        self.send_error(403, "Directory listing disabled")
        return None

    def log_message(self, format: str, *args: object) -> None:
        safe = html.escape(format % args)
        print(f"[dashboard] {self.address_string()} {safe}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KB workflow dashboard.")
    parser.add_argument("--kb-root", type=Path, default=DEFAULT_KB_ROOT)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--project-health-dir", type=Path, default=DEFAULT_PROJECT_HEALTH_DIR)
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow binding to a non-loopback host. The dashboard is read-only but exposes local KB metadata.",
    )
    args = parser.parse_args()

    if not args.allow_non_loopback and not host_is_loopback(args.host):
        parser.error("--host must be loopback unless --allow-non-loopback is set")

    kb_root = args.kb_root.expanduser().resolve()
    missing = validate_kb_root(kb_root)
    if missing:
        parser.error(f"{kb_root} is not a valid KB root; missing: {', '.join(missing)}")

    static_dir = Path(__file__).resolve().parent / "static"
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {
            "kb_root": kb_root,
            "state_db": args.state_db.expanduser().resolve(),
            "static_dir": static_dir,
            "project_health_dir": args.project_health_dir.expanduser().resolve(),
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"KB Workflow Dashboard: http://{args.host}:{args.port}")
    print(f"KB root: {handler.kb_root}")
    print("Read-only except the guarded /api/promote/{preview,execute} endpoints")
    print("(loopback-only, allowlisted topic reading-lists, kb-promote.py under the write lock, audited to command-log.jsonl).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
