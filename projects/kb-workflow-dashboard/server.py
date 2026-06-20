#!/usr/bin/env python3
"""Read-only local dashboard for the user's KB workflow."""
from __future__ import annotations

import argparse
import html
import ipaddress
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_KB_ROOT = Path.home() / "Vault" / "knowledgebase"
DEFAULT_STATE_DB = Path.home() / ".local" / "state" / "kb-feed" / "state.sqlite"
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
    current = {"heading": "Top", "unchecked": 0, "checked": 0, "failed": 0, "active": 0, "review": 0}
    current_heading = "Top"
    has_seen_content = False
    for line_number, line in visible_lines:
        heading = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if heading:
            if has_seen_content or any(current[k] for k in ("unchecked", "checked", "failed", "active", "review")):
                sections.append(current)
            current_heading = heading.group(2)
            current = {"heading": current_heading, "unchecked": 0, "checked": 0, "failed": 0, "active": 0, "review": 0}
            has_seen_content = True
            continue

        stripped = line.lstrip()
        status = ""
        item_match = CHECKBOX_RE.match(stripped)
        if item_match:
            status = CHECKBOX_STATUS[item_match.group(1).lower()]
            current[status] += 1

        if status:
            items.append(
                {
                    "status": status,
                    "section": current_heading,
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
        "recent_log": parse_recent_log(kb_root),
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    kb_root: Path
    state_db: Path
    static_dir: Path

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
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

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
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"KB Workflow Dashboard: http://{args.host}:{args.port}")
    print(f"KB root: {handler.kb_root}")
    print("Read-only MVP: no KB writes or command execution.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
