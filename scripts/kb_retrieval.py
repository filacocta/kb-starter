"""Bounded, cited retrieval from current Markdown files without a body cache."""

from dataclasses import asdict, dataclass, replace
import hashlib
from itertools import chain
import json
from pathlib import Path
import re
import sys

from kb_config import KBConfigError, iter_markdown, read_regular_bytes, read_regular_text, validate_topic_slug


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_BYTES = 64 * 1024 * 1024
NOISE_DIRS = {"archive", "archives", "staging", "daily", "runs", "_tools", "_library", "_media"}
CONTEXT_FILES = {"_summary.md", "_todos.md", "_context-map.md"}
GOVERNANCE_FILES = {"AGENTS.md", "CLAUDE.md", "SCHEMA.md", "CONTRIBUTING.md"}
CONTROL_DIRS = {"_workflows", "_templates", "templates"}
CAPTURE_DIRS = {"readinglist", "clippings"}


@dataclass(frozen=True)
class Passage:
    path: str
    line_start: int
    line_end: int
    role: str
    sha256: str
    text: str
    score: int = 0
    clipped: bool = False
    equivalent_paths: tuple[str, ...] = ()


def collapse_copies(passages):
    unique = {}
    for item in sorted(passages, key=lambda value: (-value.score, value.path, value.line_start)):
        key = (item.sha256, item.line_start, item.line_end)
        if key in unique:
            first = unique[key]
            aliases = set(first.equivalent_paths) | {item.path} | set(item.equivalent_paths)
            aliases.discard(first.path)
            unique[key] = replace(first, equivalent_paths=tuple(sorted(aliases)))
        else:
            unique[key] = item
    return list(unique.values())


def role(path):
    parts = Path(path).parts
    if not parts:
        raise KBConfigError("select a root-relative Markdown file")
    if any(part.startswith(".") or part in NOISE_DIRS for part in parts):
        return "excluded"
    if "sources" in parts or "media" in parts:
        return "source"
    directories = parts[2:-1] if parts[0] in {"topics", "projects"} else parts[:-1]
    if CONTROL_DIRS.intersection(directories):
        return "control"
    if CAPTURE_DIRS.intersection(directories):
        return "capture"
    if parts[-1] in CONTEXT_FILES:
        return "context"
    if parts[-1].startswith("_") or parts[-1] in GOVERNANCE_FILES | {"reading-list.md", "README.md"}:
        return "excluded"
    return "maintained"


def terms(value):
    return {word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word
            for word in re.findall(r"\w+", value.casefold()) if len(word) > 1}


def headings(lines):
    found = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*$", line)
        if match and fence is None:
            title = re.sub(r"\s+#+\s*$", "", match[2])
            found.append((index, len(match[1]), title))
    return found


def load(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise KBConfigError("select one root-relative Markdown path")
    text = read_regular_text(root / path, max_bytes=MAX_FILE_BYTES)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def sections(path, text, digest):
    lines = text.splitlines(keepends=True)
    start = 0
    if lines and lines[0].strip() == "---":
        ending = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if ending is not None:
            start = ending + 1
    points = sorted({start, *(i for i, _, _ in headings(lines) if i >= start), len(lines)})
    for first, end in zip(points, points[1:]):
        body = "".join(lines[first:end]).strip("\n")
        if body.strip():
            yield Passage(path, first + 1, end, role(path), digest, body)


def select(root, args):
    relative = args.read if args.read is not None else args.headings
    kind = role(relative)
    if {"sources", "media"}.intersection(Path(relative).parts) and not args.sources:
        raise KBConfigError("source evidence requires --sources")
    if kind == "excluded" and not args.include_excluded:
        raise KBConfigError("operational files require explicit --include-excluded")
    text, digest = load(root, relative)
    if args.expect_sha256 and digest != args.expect_sha256:
        raise KBConfigError("selected file changed; retrieve fresh citations first")
    lines = text.splitlines(keepends=True)
    if not lines:
        raise KBConfigError("selected file is empty")
    found = headings(lines)
    if args.headings:
        body = "\n".join(f"{i + 1}: {'#' * level} {title}" for i, level, title in found)
        return [Passage(relative, 1, len(lines), kind, digest, body)], {}
    first, end = 0, len(lines)
    if args.lines and args.heading:
        raise KBConfigError("choose --lines or --heading")
    if args.lines:
        match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", args.lines)
        if not match:
            raise KBConfigError("line selection must be START:END with positive line numbers")
        first, end = int(match[1]) - 1, int(match[2])
        if first >= end or end > len(lines):
            raise KBConfigError("line selection is outside the file")
    elif args.heading:
        matches = [(i, level) for i, level, title in found if title.casefold() == args.heading.casefold()]
        if len(matches) != 1:
            raise KBConfigError("heading must match exactly one heading; use --headings and --lines otherwise")
        first, level = matches[0]
        end = next((i for i, depth, _ in found if i > first and depth <= level), len(lines))
    return [Passage(relative, first + 1, end, kind, digest, "".join(lines[first:end]))], {}


def retrieve(root, args):
    if args.topic and args.project:
        raise KBConfigError("choose a topic or a project")
    slug = args.topic or args.project
    if slug:
        slug = validate_topic_slug(slug)
    scope = f"topics/{slug}" if args.topic else f"projects/{slug}" if args.project else None
    if getattr(args, "area", None) and scope and not scope.startswith(args.area + "/"):
        raise KBConfigError("area does not match the topic or project scope")
    if scope and not (root / scope).is_dir():
        raise KBConfigError("requested topic or project does not exist")
    if args.context and args.domain == "personal" and not scope:
        raise KBConfigError("personal context requires --topic or --project; use --body to discover it")
    query = terms(args.query or "")
    if not args.context and not query:
        raise KBConfigError("body search requires a query")
    roots = (scope,) if scope else (".",)
    if not scope and getattr(args, "area", None):
        roots = (args.area,)
    primary = []
    work_map = None
    if args.context:
        if scope:
            primary.append(f"{scope}/_summary.md")
            if args.project:
                primary.append(f"{scope}/_todos.md")
        if args.domain == "work":
            preferred = root / "_context-map.md"
            if preferred.exists() or preferred.is_symlink():
                work_map = "_context-map.md"
            else:
                maps = [str(path.relative_to(root))
                        for path in iter_markdown(root, (".",), max_entries=20000, exclude_dirs=NOISE_DIRS)
                        if path.name == "_context-map.md"
                        and role(str(path.relative_to(root))) == "context"]
                if scope:
                    scoped_maps = [path for path in maps if Path(path).is_relative_to(scope)]
                    maps = scoped_maps or [path for path in maps if Path(path).parts[0] not in {"projects", "topics"}]
                if len(maps) > 1:
                    raise KBConfigError("multiple context maps; add a root _context-map.md or select one with --read")
                work_map = maps[0] if maps else None
            if work_map:
                primary.insert(0, work_map)
    passages = []
    counts = {"files_read": 0, "bytes_read": 0, "bytes_attempted": 0, "source_files": 0}
    notices = []
    result_limit_reached = False
    primary_set = set(primary)
    entries = [root / path for path in primary if path == work_map or (root / path).is_file()]
    if args.context and not entries:
        raise KBConfigError("no context entry point exists in the requested scope")
    discovered = (path for path in iter_markdown(root, roots, max_entries=20000, exclude_dirs=NOISE_DIRS)
                  if str(path.relative_to(root)) not in primary_set)

    def candidates():
        try:
            yield from chain(entries, discovered)
        except (KBConfigError, OSError) as exc:
            notices.append(str(exc))

    for path in candidates():
        relative = str(path.relative_to(root))
        kind = role(relative)
        if kind in {"excluded", "control", "capture"} or (kind == "source" and not args.sources):
            continue
        if args.context and not query and relative not in primary_set:
            continue
        if kind == "source":
            counts["source_files"] += 1
        try:
            remaining = MAX_SCAN_BYTES - counts["bytes_attempted"]
            if remaining <= 0:
                notices.append("scan byte limit reached; absence of further matches is not established")
                break
            raw = read_regular_bytes(path, max_bytes=min(MAX_FILE_BYTES, remaining))
            counts["bytes_attempted"] += len(raw)
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise KBConfigError("file is not valid UTF-8") from exc
            digest = hashlib.sha256(raw).hexdigest()
            counts["files_read"] += 1
            counts["bytes_read"] += len(text.encode("utf-8"))
            if relative in primary_set:
                content = list(sections(relative, text, digest))
                if content:
                    first, last = content[0], content[-1]
                    lines = text.splitlines(keepends=True)
                    passages.append(Passage(relative, first.line_start, last.line_end, kind, digest,
                                            "".join(lines[first.line_start - 1:last.line_end]),
                                            100001 if relative == work_map else 100000))
                continue
            for passage in sections(relative, text, digest):
                words = terms(passage.text)
                coverage = len(query & words)
                path_hits = len(query & terms(relative))
                if query and not coverage and not path_hits:
                    continue
                heading = passage.text.splitlines()[0]
                score = coverage * 10 + len(query & terms(heading)) * 4 + path_hits
                if "/syntheses/" in relative:
                    score += 3
                passages.append(Passage(**{**asdict(passage), "score": score}))
            passages = collapse_copies(passages)
            keep = max(args.limit * 6, 30)
            result_limit_reached |= len(passages) > keep
            passages = passages[:keep]
        except (KBConfigError, OSError) as exc:
            if len(notices) < 10:
                notices.append(f"{relative}: {exc}")
    passages = collapse_copies(passages)
    return passages[:args.limit], {**counts, "notices": notices, "scan_complete": not notices,
                                   "result_limit_reached": result_limit_reached or len(passages) > args.limit,
                                   "omitted_matches": max(0, len(passages) - args.limit)}


def render(packet, budget, json_output):
    if budget < 1024 or budget > 1000000:
        raise KBConfigError("output budget must be between 1024 and 1000000 bytes")
    candidates = packet.pop("results")
    packet["results"] = []
    packet["omitted_results"] = len(candidates) + packet.pop("omitted_matches", 0)

    def encode():
        if json_output:
            return (json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        header = f"{packet['status']} | {packet['domain']} | {packet['root']}\n"
        body = "\n\n".join(
            f"[{packet['domain']}:{r['path']}:{r['line_start']}-{r['line_end']} sha256={r['sha256']}]"
            + (" [partial excerpt]" if r['clipped'] else "") + "\n" + r['text']
            + ("\nIdentical copies: " + ", ".join(r['equivalent_paths']) if r['equivalent_paths'] else "")
            for r in packet['results']
        )
        qualifier = "at least " if packet.get('result_limit_reached', False) else ""
        footer = (f"\nOmitted results: {qualifier}{packet['omitted_results']}. "
                  f"Result limit reached: {packet.get('result_limit_reached', False)}. "
                  f"Scan complete: {packet.get('scan_complete', True)}.")
        notices = "\n".join(packet.get("notices", []))
        return (header + body + footer + ("\n" + notices if notices else "") + "\n").encode("utf-8")

    if len(encode()) > budget:
        raise KBConfigError("budget cannot fit the result envelope; request a larger budget")
    for passage in candidates:
        record = asdict(passage)
        packet['results'].append(record)
        packet['omitted_results'] -= 1
        if len(encode()) <= budget:
            continue
        raw = record['text'].encode('utf-8')
        low, high = 0, len(raw)
        record['clipped'] = True
        while low < high:
            middle = (low + high + 1) // 2
            record['text'] = raw[:middle].decode('utf-8', errors='ignore')
            if len(encode()) <= budget:
                low = middle
            else:
                high = middle - 1
        record['text'] = raw[:low].decode('utf-8', errors='ignore')
        if not record['text'].strip():
            packet['results'].pop()
            packet['omitted_results'] += 1
        break
    output = encode()
    if len(output) > budget:
        raise KBConfigError("budget cannot fit cited results")
    return output


def run(root, args):
    try:
        if args.limit < 1 or args.limit > 100:
            raise KBConfigError("result limit must be between 1 and 100")
        if (args.lines or args.heading or args.expect_sha256) and not args.read:
            raise KBConfigError("line, heading and digest selectors require --read")
        if args.read is not None or args.headings is not None:
            passages, metadata = select(root, args)
        else:
            passages, metadata = retrieve(root, args)
        status = "incomplete" if metadata.get('notices') else "ok" if passages else "no_matches"
        packet = {"status": status, "domain": args.domain, "root": str(root),
                  **metadata, "results": passages}
        output = render(packet, args.budget_bytes, args.json)
        sys.stdout.buffer.write(output)
        return 3 if status == "incomplete" else 0 if passages else 1
    except (KBConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
