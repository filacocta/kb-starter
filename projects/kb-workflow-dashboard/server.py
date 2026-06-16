#!/usr/bin/env python3
"""Read-only local dashboard for the user's KB workflow."""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_KB_ROOT = Path.home() / "Vault" / "knowledgebase"
DEFAULT_STATE_DB = Path.home() / ".local" / "state" / "kb-feed" / "state.sqlite"


@dataclass
class QueueFile:
    name: str
    path: str
    unchecked: int
    checked: int
    failed: int
    sections: list[dict[str, object]]


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


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


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
    return {
        "unchecked": sum(1 for line in lines if line.lstrip().startswith("- [ ]")),
        "checked": sum(1 for line in lines if line.lstrip().startswith("- [x]")),
        "failed": sum(1 for line in lines if line.lstrip().startswith("- [!]")),
        "active": sum(1 for line in lines if line.lstrip().startswith("- [~]")),
    }


def parse_queue_file(path: Path, kb_root: Path, label: str | None = None) -> QueueFile:
    text = strip_html_comments(strip_frontmatter(read_text(path)))
    lines = text.splitlines()
    sections: list[dict[str, object]] = []
    current = {"heading": "Top", "unchecked": 0, "checked": 0, "failed": 0, "active": 0}
    has_seen_content = False
    for line in lines:
        heading = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if heading:
            if has_seen_content or any(current[k] for k in ("unchecked", "checked", "failed", "active")):
                sections.append(current)
            current = {"heading": heading.group(2), "unchecked": 0, "checked": 0, "failed": 0, "active": 0}
            has_seen_content = True
            continue
        stripped = line.lstrip()
        if stripped.startswith("- [ ]"):
            current["unchecked"] += 1
        elif stripped.startswith("- [x]"):
            current["checked"] += 1
        elif stripped.startswith("- [!]"):
            current["failed"] += 1
        elif stripped.startswith("- [~]"):
            current["active"] += 1
    if has_seen_content or any(current[k] for k in ("unchecked", "checked", "failed", "active")):
        sections.append(current)
    counts = checkbox_counts(lines)
    return QueueFile(
        name=label or path.parent.name,
        path=relpath(path, kb_root),
        unchecked=counts["unchecked"],
        checked=counts["checked"],
        failed=counts["failed"],
        sections=sections,
    )


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
                "queue": asdict(queue) if queue else None,
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
        todo_counts = checkbox_counts(strip_frontmatter(read_text(todos)).splitlines()) if todos.exists() else {}
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


def parse_dive_queue(kb_root: Path) -> QueueFile:
    return parse_queue_file(kb_root / "_dive-queue.md", kb_root, label="Research Dive Queue")


def parse_recent_log(kb_root: Path, limit: int = 5) -> list[dict[str, str]]:
    text = strip_frontmatter(read_text(kb_root / "_log.md"))
    entries = re.split(r"\n(?=## \[\d{4}-\d{2}-\d{2}\])", text)
    out: list[dict[str, str]] = []
    for entry in entries:
        if not entry.startswith("## ["):
            continue
        lines = entry.strip().splitlines()
        title = lines[0].replace("## ", "", 1)
        summary = " ".join(line.strip() for line in lines[1:4] if line.strip())
        out.append({"title": title, "summary": summary})
        if len(out) >= limit:
            break
    return out


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


def build_status(kb_root: Path, state_db: Path) -> dict[str, object]:
    links = parse_queue_file(kb_root / "readinglist" / "Links.md", kb_root, label="URL Inbox")
    topics_inbox = parse_queue_file(kb_root / "readinglist" / "topics.md", kb_root, label="Topic Interests")
    ideas = parse_queue_file(kb_root / "ideas" / "_backlog.md", kb_root, label="Ideas Backlog")
    topics = parse_topics(kb_root)
    projects = parse_projects(kb_root)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_root": str(kb_root),
        "checkpoint": parse_checkpoint(kb_root),
        "global_queues": [asdict(links), asdict(topics_inbox), asdict(ideas)],
        "dive_queue": asdict(parse_dive_queue(kb_root)),
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
            self.send_json(build_status(self.kb_root, self.state_db))
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def send_json(self, data: object) -> None:
        encoded = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    args = parser.parse_args()

    static_dir = Path(__file__).resolve().parent / "static"
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {
            "kb_root": args.kb_root.expanduser().resolve(),
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
