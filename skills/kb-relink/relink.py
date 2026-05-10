#!/usr/bin/env python3
"""kb-relink driver script.

Implements the wikilink + tag enrichment passes described in the kb-relink
SKILL.md. Designed to be invoked as a subprocess by the LLM-side skill so
that scanning runs deterministically with one driver per invocation.

Pass 1 (this file) covers WIKILINKS only. Pass 2 will bolt on tag enrichment.

Exit codes:
  0  -- ran successfully (regardless of whether edits were proposed)
  1  -- usage / argument error
  2  -- unrecoverable runtime error (e.g. KB not found)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Iterable

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KB_ROOT_DEFAULT = Path.home() / "Documents" / "worklog" / "knowledgebase"  # adjust to your KB root, or override with --kb-root
SKILL_DIR = Path(__file__).resolve().parent
ALIASES_FILE = SKILL_DIR / "aliases.yaml"
TAG_RULES_FILE = SKILL_DIR / "tag-rules.yaml"

# Obsidian CLI — used by Pass 4 (incoming-refresh) to fetch backlinks.
# Falls back to filesystem scan if unavailable.
OBSIDIAN_CLI = Path.home() / ".local/bin/obsidian"

# Cap on `incoming:` list size per note. Highly-referenced anchors (topic
# entities) can have 50+ backlinks; storing all of them would bloat frontmatter
# without improving usability. The top-N by some ordering are kept.
INCOMING_MAX = 10

# Structural files at the KB root or topic root that are NEVER scanned/edited
# (they're auto-generated, machine-managed, or hold rules/schema). NOTE that
# `_summary.md` is intentionally NOT in this list — summaries are regular
# content edited by the user, and should participate in relink.
STRUCTURAL_FILENAMES = {
    "_index.md", "_checkpoint.md", "_schema.md", "_rules.md", "_dive-queue.md",
    "_todo.md", "_log.md", "_relink-report.md", "_workflow-checklist.md",
    "_no-emojis.md",
}
EXCLUDE_FILENAMES = {"README.md", "reading-list.md"}
EXCLUDE_DIRS = {".obsidian", ".git", ".trash", "bases", "_research-inbox", "reading-list"}
# Specific files that must never be touched (machine-managed / append-only).
EXCLUDE_PATHS = {
    "topics/personal-notes/notes/entities/mark-todos.md",
}
EXCLUDE_DAILY = ("topics/personal-notes/notes/daily/",)

# Generic phrases that must never be auto-linked even if a slug matches them.
# Plurals included because the slug-derivation step doesn't pluralize.
HARD_SKIP_PHRASES = {
    "format", "formats", "container", "containers", "stream", "streams",
    "frame", "frames", "packet", "packets", "protocol", "protocols",
    "model", "models", "video", "videos", "audio", "data", "service",
    "services", "pipeline", "pipelines", "system", "systems", "library",
    "libraries", "api", "apis", "client", "clients", "server", "servers",
    "agent", "agents", "note", "notes", "source", "sources", "topic", "topics",
    "layer", "layers",   # `layers` entity is about Lambda layers, but prose
                          # uses "layers" generically (architectural layers, etc.)
    "link", "links",     # LINKS.md in reading-list dirs; "link" is too generic
    "wikipedia",         # wikipedia.md entity is LLM-topic-specific; "Wikipedia"
                         # appears ubiquitously in source citation rows as attribution
}

# Suffixes on anchor slugs that are stripped before deriving common-noun phrases.
# `-overview` slugs are excluded from the heuristic entirely (handled below) —
# they're survey notes whose remaining slug fragment is usually too generic
# to safely match in prose (e.g. `containers-overview` -> "containers").
SLUG_SUFFIX_STRIPS = ("-entity", "-deep-dive")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Anchor:
    slug: str
    path: Path
    type: str
    topic: str
    title: str | None
    display_phrases: list[str] = field(default_factory=list)


@dataclass
class WikilinkProposal:
    file_rel: str
    line: int                  # 1-indexed, FILE-relative
    body_offset: int           # offset within body (post-frontmatter)
    end_offset: int            # exclusive
    phrase: str
    anchor_slug: str

    def to_dict(self) -> dict:
        return {
            "file": self.file_rel,
            "line": self.line,
            "phrase": self.phrase,
            "anchor": self.anchor_slug,
        }


def file_line_for_offset(text: str, body_start: int, body_offset: int) -> int:
    """Return 1-indexed FILE-relative line number for a body offset."""
    return text.count("\n", 0, body_start + body_offset) + 1


@dataclass
class TagProposal:
    file_rel: str
    tag: str                   # with leading "#"
    trigger_type: str          # "phrase" or "via_anchor"
    evidence: str

    def to_dict(self) -> dict:
        return {
            "file": self.file_rel,
            "tag": self.tag,
            "trigger": self.trigger_type,
            "evidence": self.evidence,
        }


@dataclass
class BareTopicProposal:
    """A bare `[[topic-slug]]` wikilink that should be rewritten to
    `[[topic-slug/_summary|<Display>]]` because the target file `topic-slug.md`
    doesn't exist anywhere in the KB but `topics/topic-slug/_summary.md` does.
    """
    file_rel: str
    line: int                  # 1-indexed, file-relative
    body_offset: int
    end_offset: int
    topic_slug: str
    display: str               # display text for the rewritten link

    def to_dict(self) -> dict:
        return {
            "file": self.file_rel,
            "line": self.line,
            "topic": self.topic_slug,
            "display": self.display,
        }


@dataclass
class TagRule:
    tag: str                   # with leading "#"
    phrases: list[str] = field(default_factory=list)
    via_anchor: list[str] = field(default_factory=list)


@dataclass
class FileResult:
    path: Path
    rel_path: str
    wikilinks: list[WikilinkProposal] = field(default_factory=list)
    tag_proposals: list[TagProposal] = field(default_factory=list)
    bare_topics: list[BareTopicProposal] = field(default_factory=list)
    skipped_unverified: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frontmatter & file IO
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter_dict_or_empty, body_text, body_start_offset)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return ({}, text, 0)
    raw = m.group(1)
    body_start = m.end()
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return (fm, text[body_start:], body_start)


# ---------------------------------------------------------------------------
# Anchor inventory build
# ---------------------------------------------------------------------------


def is_excluded_path(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    name = Path(rel).name
    if name in EXCLUDE_FILENAMES:
        return True
    if name in STRUCTURAL_FILENAMES:
        return True
    if rel in EXCLUDE_PATHS:
        return True
    if any(rel.startswith(p) for p in EXCLUDE_DAILY):
        return True
    if name.endswith(".base"):
        return True
    # Source notes (topics/<topic>/sources/*.md) are immutable historical
    # records — never modify them, regardless of pass.
    if "sources" in parts:
        return True
    return False


def is_anchor_source(rel: str) -> bool:
    """True if this file is allowed to be the SOURCE of an anchor (i.e. linkable to).

    Note: this is stricter than `is_excluded_path`. Even files we DO scan/edit
    (like `_summary.md`) may not be valid anchor sources, because their slug
    would collide with every other topic's `_summary.md`. Pass 3 (bare-topic
    rewrite) handles topic summaries via path-style links separately.
    """
    if is_excluded_path(rel):
        return False
    parts = Path(rel).parts
    if "sources" in parts:
        # source notes are immutable; never link inline TO them
        return False
    name = Path(rel).name
    if name == "_summary.md":
        # Topic summaries — not slug-linkable (slug would be `_summary` for all
        # topics). Linked via Pass 3 path-style instead.
        return False
    return True


def derive_common_noun_phrases(slug: str, anchor_type: str) -> list[str]:
    """Step 3 heuristic: derive common-noun phrases from a slug."""
    if anchor_type == "synthesis":
        # synthesis slugs are long compound phrases; only explicit aliases.
        return []
    if slug.endswith("-overview"):
        # overview slugs are survey notes whose remaining fragment is usually
        # too generic ("containers-overview" -> "containers"). Use only
        # explicit aliases.
        return []
    base = slug
    for suffix in SLUG_SUFFIX_STRIPS:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    parts = base.split("-")
    if len(parts) == 1 and parts[0]:
        # single token: emit several casings IN ORDER (upper, capitalize, lower)
        # so the acronym form is preferred when matches collide on lowercase.
        token = parts[0]
        ordered = [token.upper(), token.capitalize(), token.lower(), token]
        # dedup preserving order, keep distinct casings as separate phrases
        seen: set[str] = set()
        out: list[str] = []
        for c in ordered:
            if c in seen:
                continue
            if c.lower() in HARD_SKIP_PHRASES:
                continue
            seen.add(c)
            out.append(c)
        return out
    # multi-token: emit the joined original as a candidate; do NOT split into
    # individual common nouns automatically (that's where false positives live).
    joined = " ".join(parts)
    return [joined] if joined.lower() not in HARD_SKIP_PHRASES else []


def load_aliases_yaml() -> dict[str, list[str]]:
    if not ALIASES_FILE.exists():
        return {}
    try:
        data = yaml.safe_load(ALIASES_FILE.read_text()) or {}
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}
    except yaml.YAMLError:
        return {}


def build_anchor_inventory(kb_root: Path) -> dict[str, Anchor]:
    """Walk the KB and produce a slug -> Anchor map."""
    aliases = load_aliases_yaml()
    inventory: dict[str, Anchor] = {}

    for md_path in (kb_root / "topics").rglob("*.md"):
        rel = str(md_path.relative_to(kb_root))
        if not is_anchor_source(rel):
            continue
        try:
            text = md_path.read_text()
        except OSError:
            continue
        fm, _body, _ = split_frontmatter(text)
        if "type" not in fm:
            continue  # likely scratch
        slug = md_path.stem
        if slug in inventory:
            # collision -- keep first; warn via stderr
            print(f"warn: anchor collision {slug}: ignoring {rel}", file=sys.stderr)
            continue
        anchor = Anchor(
            slug=slug,
            path=md_path,
            type=str(fm.get("type", "")),
            topic=str(fm.get("topic", "")),
            title=fm.get("title"),
        )
        # display phrases: title + aliases (frontmatter) + heuristic + curated
        phrases: list[str] = []
        if anchor.title:
            phrases.append(str(anchor.title))
        fm_aliases = fm.get("aliases") or []
        if isinstance(fm_aliases, list):
            phrases.extend(str(a) for a in fm_aliases)
        phrases.extend(derive_common_noun_phrases(slug, anchor.type))
        phrases.extend(aliases.get(slug, []))
        # de-dup by EXACT case (not lowercase) so distinct casings of the same
        # token survive — "PyAV" and "pyav" match in different contexts.
        # Drop empty / hard-skip.
        seen: set[str] = set()
        clean: list[str] = []
        for p in phrases:
            p = p.strip()
            if not p or p.lower() in HARD_SKIP_PHRASES:
                continue
            if p in seen:
                continue
            seen.add(p)
            clean.append(p)
        anchor.display_phrases = clean
        inventory[slug] = anchor

    return inventory


# ---------------------------------------------------------------------------
# Safe-zone identification
# ---------------------------------------------------------------------------


# Patterns that yield UNSAFE spans (relative to body offsets).
# Order matters: fenced code blocks first (multi-line), then per-line patterns.

INLINE_PATTERNS = [
    # ATX headings (#, ##, ..., ######) — entire line including trailing newline
    re.compile(r"^#{1,6}\s.*$", re.MULTILINE),
    # Wikilinks (aliased and plain)
    re.compile(r"\[\[[^\]]+\]\]"),
    # Markdown links: capture the [text](url) entirely
    re.compile(r"\[[^\]]*\]\([^)]*\)"),
    # Inline code (single or double backticks)
    re.compile(r"``[^`]+``|`[^`\n]+`"),
    # Bare URLs
    re.compile(r"https?://\S+"),
    # HTML / Obsidian comments
    re.compile(r"<!--.*?-->", re.DOTALL),
]

FENCE_RE = re.compile(r"^(```|~~~)", re.MULTILINE)
INDENTED_CODE_LINE = re.compile(r"^(?: {4}|\t)", re.MULTILINE)


def find_unsafe_spans(body: str) -> list[tuple[int, int]]:
    """Return list of (start, end) byte offsets in `body` that must not be touched."""
    spans: list[tuple[int, int]] = []

    # 1. Fenced code blocks. Walk fences in order; toggle on/off.
    fences = list(FENCE_RE.finditer(body))
    open_idx = None
    for i, m in enumerate(fences):
        if open_idx is None:
            open_idx = i
        else:
            # close
            start = fences[open_idx].start()
            # extend through end-of-line of the closing fence
            end_line_break = body.find("\n", m.end())
            end = end_line_break + 1 if end_line_break != -1 else len(body)
            spans.append((start, end))
            open_idx = None
    # If a fence is left open, treat the rest of the file as code.
    if open_idx is not None:
        spans.append((fences[open_idx].start(), len(body)))

    # 2. Indented code blocks. A run of consecutive 4-space-indented lines,
    #    each preceded by a blank line OR the start of body, is a code block.
    #    Simpler heuristic: any line starting with 4 spaces or a tab is unsafe
    #    UNLESS we're already inside a fenced span (handled by `is_safe_offset`
    #    below picking the union).
    for m in INDENTED_CODE_LINE.finditer(body):
        # consume to end-of-line
        line_end = body.find("\n", m.start())
        if line_end == -1:
            line_end = len(body)
        else:
            line_end += 1
        spans.append((m.start(), line_end))

    # 3. Inline patterns
    for pat in INLINE_PATTERNS:
        for m in pat.finditer(body):
            spans.append((m.start(), m.end()))

    # Merge overlapping spans
    if not spans:
        return spans
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def offset_in_spans(offset: int, spans: list[tuple[int, int]]) -> bool:
    # binary search would be faster; linear is fine for KB-sized files.
    for s, e in spans:
        if s <= offset < e:
            return True
        if s > offset:
            return False
    return False


# ---------------------------------------------------------------------------
# Section tracking
# ---------------------------------------------------------------------------


def section_headers(body: str) -> list[tuple[int, str]]:
    """Return list of (offset, heading_text) for each `^## ` heading."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"^##\s+(.+)$", body, flags=re.MULTILINE):
        out.append((m.start(), m.group(1).strip()))
    return out


def section_for(offset: int, headers: list[tuple[int, str]]) -> str:
    """Return the ## heading that contains `offset`. Empty string for lead paragraph."""
    current = ""
    for off, h in headers:
        if off > offset:
            break
        current = h
    return current


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def resolve_scope(kb_root: Path, scope: str | None) -> list[Path]:
    """Return list of .md files to scan."""
    if not scope:
        base = kb_root / "topics"
    else:
        candidate = kb_root / scope
        if candidate.is_dir():
            base = candidate
        else:
            # might be a topic slug
            topic_dir = kb_root / "topics" / scope
            if topic_dir.is_dir():
                base = topic_dir
            else:
                # treat as a single file
                if candidate.is_file():
                    return [candidate]
                raise FileNotFoundError(f"scope not found: {scope}")
    paths: list[Path] = []
    for p in base.rglob("*.md"):
        rel = str(p.relative_to(kb_root))
        if is_excluded_path(rel):
            continue
        paths.append(p)
    return sorted(paths)


# ---------------------------------------------------------------------------
# Wikilink scan (Step 4 + 4.5)
# ---------------------------------------------------------------------------


def is_word_boundary_match(body: str, start: int, end: int) -> bool:
    """Verify the match at [start, end) is at word boundaries.

    Rejects hyphen-adjacent matches: in `imageio-ffmpeg`, neither `imageio`
    nor `ffmpeg` should match because the hyphen indicates a compound
    identifier (package name, multi-word ID) rather than two separate
    references in prose. Treating `-` as a non-boundary character is
    conservative — it loses some edge cases like "open-source PyAV" but
    eliminates a much larger class of false positives.
    """
    if start > 0:
        prev = body[start - 1]
        if prev.isalnum() or prev == "_" or prev == "-":
            return False
    if end < len(body):
        nxt = body[end]
        if nxt.isalnum() or nxt == "_" or nxt == "-":
            return False
    return True


def line_for_offset(body: str, offset: int) -> int:
    """1-indexed line number within body (frontmatter-stripped)."""
    return body.count("\n", 0, offset) + 1


def scan_file_wikilinks(
    md_path: Path,
    rel: str,
    body: str,
    frontmatter_line_offset: int,
    inventory: dict[str, Anchor],
    own_slug: str,
    own_title: str | None,
    max_per_section: int,
) -> tuple[list[WikilinkProposal], list[dict]]:
    """Return (proposals, skipped_unverified)."""
    proposals: list[WikilinkProposal] = []
    skipped: list[dict] = []

    unsafe = find_unsafe_spans(body)
    headers = section_headers(body)
    own_title_lower = (own_title or "").lower().strip()

    # Per (section, anchor_slug) -> count of accepted proposals.
    # Pre-populate from existing wikilinks so a section that already has
    # [[slug|phrase]] or [[path/to/slug]] doesn't generate a second proposal
    # for that anchor. Takes the last path segment so path-style links like
    # [[notes/concepts/collective-unconscious]] resolve to their slug correctly.
    section_anchor_count: dict[tuple[str, str], int] = {}
    _existing_wl_re = re.compile(r'\[\[([^\]\|#]+)(?:\|[^\]]*)?\]\]')
    for _m in _existing_wl_re.finditer(body):
        _target = _m.group(1).strip()
        _slug = _target.split("/")[-1]
        if _slug in inventory:
            _sec = section_for(_m.start(), headers)
            section_anchor_count[(_sec, _slug)] = section_anchor_count.get((_sec, _slug), 0) + 1

    # Sort anchors by display-phrase length descending so longer phrases win on overlap.
    anchor_phrase_pairs: list[tuple[Anchor, str]] = []
    for anchor in inventory.values():
        if anchor.slug == own_slug:
            continue
        for phrase in anchor.display_phrases:
            if phrase.lower() == own_title_lower:
                continue
            anchor_phrase_pairs.append((anchor, phrase))
    anchor_phrase_pairs.sort(key=lambda ap: -len(ap[1]))

    # Track accepted ranges so a longer phrase taking [a,b] blocks a shorter
    # phrase from claiming any sub-range of it.
    accepted_ranges: list[tuple[int, int]] = []

    def overlaps_accepted(s: int, e: int) -> bool:
        for a, b in accepted_ranges:
            if not (e <= a or s >= b):
                return True
        return False

    for anchor, phrase in anchor_phrase_pairs:
        # decide case sensitivity: if phrase has any uppercase, do case-sensitive,
        # else case-insensitive. This matches the SKILL.md heuristic.
        case_sensitive = any(c.isupper() for c in phrase)
        flags = 0 if case_sensitive else re.IGNORECASE
        # use re.escape so phrases like "H.264" or "C++" are literal.
        pattern = re.compile(re.escape(phrase), flags)
        for m in pattern.finditer(body):
            start, end = m.start(), m.end()
            if not is_word_boundary_match(body, start, end):
                continue
            if offset_in_spans(start, unsafe) or offset_in_spans(end - 1, unsafe):
                continue
            if overlaps_accepted(start, end):
                continue
            section = section_for(start, headers)
            key = (section, anchor.slug)
            if section_anchor_count.get(key, 0) >= max_per_section:
                continue
            section_anchor_count[key] = section_anchor_count.get(key, 0) + 1
            accepted_ranges.append((start, end))
            proposals.append(WikilinkProposal(
                file_rel=rel,
                line=line_for_offset(body, start) + frontmatter_line_offset,
                body_offset=start,
                end_offset=end,
                phrase=body[start:end],   # preserve original casing/punct
                anchor_slug=anchor.slug,
            ))

    accepted_ranges.sort()
    return proposals, skipped


# ---------------------------------------------------------------------------
# Apply edits
# ---------------------------------------------------------------------------


def apply_wikilink_edits(body: str, proposals: list[WikilinkProposal]) -> str:
    """Apply edits bottom-up so earlier offsets stay valid."""
    out = body
    for p in sorted(proposals, key=lambda x: -x.body_offset):
        original = out[p.body_offset:p.end_offset]
        if original != p.phrase:
            # safety: text shifted; skip
            continue
        replacement = (
            f"[[{p.anchor_slug}]]"
            if original == p.anchor_slug
            else f"[[{p.anchor_slug}|{original}]]"
        )
        out = out[: p.body_offset] + replacement + out[p.end_offset:]
    return out


# ---------------------------------------------------------------------------
# Topic-summary inventory (Pass 3 — bare-topic rewrite)
# ---------------------------------------------------------------------------


def build_topic_summary_inventory(kb_root: Path) -> dict[str, tuple[Path, str]]:
    """Walk topics/<slug>/_summary.md and return slug -> (path, display_title)."""
    out: dict[str, tuple[Path, str]] = {}
    topics_root = kb_root / "topics"
    if not topics_root.is_dir():
        return out
    for topic_dir in topics_root.iterdir():
        if not topic_dir.is_dir():
            continue
        summary = topic_dir / "_summary.md"
        if not summary.is_file():
            continue
        slug = topic_dir.name
        try:
            text = summary.read_text()
        except OSError:
            continue
        fm, _, _ = split_frontmatter(text)
        title = fm.get("title")
        if not title:
            # default: titlecase of slug with hyphens -> spaces
            title = slug.replace("-", " ").title()
        out[slug] = (summary, str(title))
    return out


def build_existing_filename_set(kb_root: Path) -> set[str]:
    """All .md filenames (without extension) anywhere in the KB. Used to decide
    whether `[[X]]` would resolve to an existing file."""
    out: set[str] = set()
    for p in kb_root.rglob("*.md"):
        out.add(p.stem)
    return out


# ---------------------------------------------------------------------------
# Bare-topic scan (Pass 3)
# ---------------------------------------------------------------------------


# Match `[[X]]` and `[[X|display]]` — capture the target before any `|`, `#`, or `/`
_BARE_WIKILINK_RE = re.compile(r"\[\[([^\]\|#/]+)(?:\|[^\]]*)?\]\]")


def scan_file_bare_topics(
    rel: str,
    body: str,
    frontmatter_line_offset: int,
    own_slug: str,
    topic_summaries: dict[str, tuple[Path, str]],
    existing_filenames: set[str],
) -> list[BareTopicProposal]:
    """Find bare `[[topic-slug]]` wikilinks where:
      - slug is a known topic with `topics/<slug>/_summary.md`
      - slug is NOT an existing file anywhere in the KB
      - slug is not the current file's own slug
    Propose rewrite to `[[topic-slug/_summary|<display>]]`.
    """
    proposals: list[BareTopicProposal] = []
    for m in _BARE_WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        if target == own_slug:
            continue
        if target not in topic_summaries:
            continue
        if target in existing_filenames:
            # `[[X]]` would already resolve to some X.md somewhere; not a bare-topic case
            continue
        _, display = topic_summaries[target]
        line = line_for_offset(body, m.start()) + frontmatter_line_offset
        proposals.append(BareTopicProposal(
            file_rel=rel,
            line=line,
            body_offset=m.start(),
            end_offset=m.end(),
            topic_slug=target,
            display=str(display),
        ))
    return proposals


def apply_bare_topic_edits(body: str, proposals: list[BareTopicProposal]) -> str:
    """Rewrite `[[X]]` (or `[[X|display]]`) to `[[X/_summary|<display>]]`.
    Bottom-up so offsets stay valid."""
    out = body
    for p in sorted(proposals, key=lambda x: -x.body_offset):
        original = out[p.body_offset:p.end_offset]
        # Re-verify the original still matches a bare wikilink to this slug
        m = _BARE_WIKILINK_RE.fullmatch(original)
        if m is None or m.group(1).strip() != p.topic_slug:
            continue
        replacement = f"[[{p.topic_slug}/_summary|{p.display}]]"
        out = out[: p.body_offset] + replacement + out[p.end_offset:]
    return out


# ---------------------------------------------------------------------------
# Tag inventory build (Step 2.5)
# ---------------------------------------------------------------------------


def load_tag_rules() -> dict[str, TagRule]:
    """Load curated tag-rules.yaml. Returns tag (with #) -> TagRule."""
    if not TAG_RULES_FILE.exists():
        return {}
    try:
        data = yaml.safe_load(TAG_RULES_FILE.read_text()) or {}
    except yaml.YAMLError:
        return {}
    out: dict[str, TagRule] = {}
    for tag, spec in data.items():
        if not isinstance(tag, str) or not tag.startswith("#"):
            continue
        spec = spec or {}
        rule = TagRule(
            tag=tag,
            phrases=[str(p) for p in (spec.get("phrases") or []) if p],
            via_anchor=[str(a) for a in (spec.get("via_anchor") or []) if a],
        )
        out[tag] = rule
    return out


# ---------------------------------------------------------------------------
# Existing tag / link extraction
# ---------------------------------------------------------------------------


_NORMALIZE_TAG = re.compile(r"^#?")


def normalize_tag(tag: str) -> str:
    """Return tag with leading '#' and lowercased body."""
    t = tag.strip()
    if t.startswith("#"):
        t = t[1:]
    return f"#{t.lower()}"


def extract_existing_tags(fm: dict) -> set[str]:
    """Return set of normalized tags from frontmatter `tags:` (handles list/str/null)."""
    raw = fm.get("tags")
    if raw is None:
        return set()
    if isinstance(raw, str):
        # could be "tag1, tag2" or just "tag1"
        return {normalize_tag(t) for t in raw.replace(",", " ").split() if t}
    if isinstance(raw, list):
        return {normalize_tag(str(t)) for t in raw if t}
    return set()


_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:\|[^\]]*)?\]\]")


def extract_outgoing_anchors(body: str) -> set[str]:
    """Return set of anchor slugs referenced via [[wikilinks]] in body."""
    out: set[str] = set()
    for m in _WIKILINK_RE.finditer(body):
        out.add(m.group(1).strip())
    return out


# ---------------------------------------------------------------------------
# Tag scan (Step 4.6 + 4.7)
# ---------------------------------------------------------------------------


PHRASE_TRIGGER_MIN = 5   # require this many safe-zone phrase matches to fire
VIA_ANCHOR_TRIGGER_MIN = 2   # require this many distinct anchors from via_anchor


def scan_file_tags(
    rel: str,
    body: str,
    own_slug: str,
    existing_tags: set[str],
    outgoing_anchors: set[str],
    tag_rules: dict[str, TagRule],
) -> list[TagProposal]:
    """Step 4.6 + 4.7. Return tag proposals for one file.

    Confidence thresholds: a single passing mention isn't enough — a note that
    happens to use a phrase once shouldn't get auto-tagged. We require
    PHRASE_TRIGGER_MIN safe-zone phrase matches OR VIA_ANCHOR_TRIGGER_MIN
    distinct anchors from the rule's via_anchor list.
    """
    proposals: list[TagProposal] = []
    unsafe = find_unsafe_spans(body)

    for tag, rule in tag_rules.items():
        if normalize_tag(tag) in existing_tags:
            continue
        # Self-tag exemption: don't propose a tag whose via_anchor list contains this file's slug
        if own_slug in rule.via_anchor:
            continue

        # Phrase trigger: count matches across all phrases for this tag
        phrase_match_count = 0
        first_phrase: str | None = None
        for phrase in rule.phrases:
            case_sensitive = any(c.isupper() for c in phrase)
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(re.escape(phrase), flags)
            for m in pattern.finditer(body):
                start, end = m.start(), m.end()
                if not is_word_boundary_match(body, start, end):
                    continue
                if offset_in_spans(start, unsafe) or offset_in_spans(end - 1, unsafe):
                    continue
                phrase_match_count += 1
                if first_phrase is None:
                    first_phrase = phrase

        if phrase_match_count >= PHRASE_TRIGGER_MIN and first_phrase is not None:
            proposals.append(TagProposal(
                file_rel=rel, tag=tag, trigger_type="phrase",
                evidence=f"{first_phrase} (×{phrase_match_count})",
            ))
            continue

        # via_anchor trigger: count distinct anchors from the list present in outgoing
        matched_anchors = [s for s in rule.via_anchor if s in outgoing_anchors]
        if len(matched_anchors) >= VIA_ANCHOR_TRIGGER_MIN:
            proposals.append(TagProposal(
                file_rel=rel, tag=tag, trigger_type="via_anchor",
                evidence=f"{', '.join(matched_anchors)}",
            ))

    return proposals


# ---------------------------------------------------------------------------
# Tag edit application (Step 5 — tags portion)
# ---------------------------------------------------------------------------


def apply_tag_edits(text: str, body_start: int, new_tags: list[str]) -> str:
    """Insert new tags into the frontmatter `tags:` field.

    Preserves flow vs. block style. Tags arrive WITH leading "#"; we strip
    the # for storage in `tags:` (Obsidian convention -- the # is implicit).

    Returns the full text (frontmatter + body) with tags merged.
    """
    if not new_tags:
        return text
    bare = [t.lstrip("#") for t in new_tags]
    fm_text = text[:body_start]

    # Find the tags: line and its block (if block-style).
    tag_line = re.search(r"(?m)^tags:\s*(.*)$", fm_text)
    if tag_line is None:
        # No tags: line. Insert before the closing `---`.
        # Locate the closing --- line within frontmatter.
        m = re.search(r"\n---\s*\n", fm_text)
        if m is None:
            return text
        insertion = f"tags: [{', '.join(bare)}]\n"
        new_fm = fm_text[: m.start() + 1] + insertion + fm_text[m.start() + 1 :]
        return new_fm + text[body_start:]

    line_start = tag_line.start()
    rhs = tag_line.group(1).strip()

    # Flow-style: tags: [a, b]
    if rhs.startswith("["):
        # find the closing bracket
        close = fm_text.find("]", tag_line.start())
        if close == -1:
            return text  # malformed; skip
        inside = fm_text[tag_line.end() - len(tag_line.group(1)) : close + 1]
        # parse current items conservatively from rhs (between brackets)
        bracket_open = rhs.find("[") + tag_line.end() - len(tag_line.group(1))
        bracket_close = close
        # rebuild: keep original inside contents, append new bare tags
        inner_text = fm_text[bracket_open + 1 : bracket_close].strip()
        existing_items = [s.strip() for s in inner_text.split(",") if s.strip()]
        merged = existing_items + bare
        new_inside = ", ".join(merged)
        new_fm = fm_text[:bracket_open + 1] + new_inside + fm_text[bracket_close:]
        return new_fm + text[body_start:]

    # Block-style: tags: (then list items below) OR tags: <single>
    if rhs == "":
        # list items follow on subsequent lines starting with "- "
        # find the end of the block (first line that doesn't start with "  -" or "- ")
        block_start = tag_line.end() + 1   # past the trailing newline of tags: line
        i = block_start
        list_re = re.compile(r"(?m)^[ \t]*-\s+.+$")
        last_end = tag_line.end()
        # walk forward line-by-line
        while i < len(fm_text):
            line_end = fm_text.find("\n", i)
            if line_end == -1:
                line_end = len(fm_text)
            line = fm_text[i:line_end]
            if list_re.match(line):
                last_end = line_end
                i = line_end + 1
                continue
            break
        # Insert new items right after last_end
        addition = "".join(f"\n  - {t}" for t in bare)
        new_fm = fm_text[:last_end] + addition + fm_text[last_end:]
        return new_fm + text[body_start:]

    # Inline single value: tags: foo
    # Convert to flow style: tags: [foo, new1, new2]
    line_end = fm_text.find("\n", line_start)
    if line_end == -1:
        line_end = len(fm_text)
    items = [rhs.strip()] + bare
    new_line = f"tags: [{', '.join(items)}]"
    new_fm = fm_text[:line_start] + new_line + fm_text[line_end:]
    return new_fm + text[body_start:]


def verify_yaml_parses(text: str) -> bool:
    fm, _, _ = split_frontmatter(text)
    return isinstance(fm, dict)


def safe_write(path: Path, content: str, original_mtime_ns: int) -> bool:
    """Atomically write `content` to `path`, but only if the file's mtime
    hasn't changed since `original_mtime_ns` (the time it was read). Returns
    True if written, False if a concurrent modification was detected and the
    write was skipped.

    Critical for KB editing because Obsidian Sync continuously rewrites the
    vault. A non-atomic write on a freshly synced file produces partial
    content (the corruption seen on 2026-05-01 mid-relink — see synthesis).
    """
    try:
        current_mtime = path.stat().st_mtime_ns
    except OSError:
        # file vanished between read and write — abort the edit
        print(f"warn: file vanished, skipping: {path}", file=sys.stderr)
        return False
    if current_mtime != original_mtime_ns:
        print(f"warn: concurrent modification detected, skipping: {path} "
              f"(was mtime={original_mtime_ns}, now={current_mtime})",
              file=sys.stderr)
        return False
    # Write atomically: temp file in same dir, then os.rename. The rename is
    # atomic on POSIX, so readers either see the old file or the new one,
    # never a half-written state.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content)
        os.replace(tmp, path)
    except OSError as e:
        print(f"warn: write failed for {path}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


# ---------------------------------------------------------------------------
# Pass 4: incoming-link snapshot in frontmatter
# ---------------------------------------------------------------------------


def query_backlinks(slug: str) -> list[str]:
    """Call `obsidian backlinks file=<slug>` and return list of paths.
    Returns [] if CLI is unavailable or returns nothing.
    """
    if not OBSIDIAN_CLI.is_file():
        return []
    try:
        result = subprocess.run(
            [str(OBSIDIAN_CLI), "backlinks", f"file={slug}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # CLI returns paths relative to the vault root, e.g.
        # "knowledgebase/topics/..."—we want the path relative to KB root
        # ("topics/..."). Strip the leading "knowledgebase/" if present.
        if line.startswith("knowledgebase/"):
            line = line[len("knowledgebase/"):]
        paths.append(line)
    return paths


def normalize_incoming_paths(paths: list[str], own_rel: str) -> list[str]:
    """Filter, dedup, sort, and cap the incoming-paths list."""
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p == own_rel:           # don't list self
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    # Sort for stability across runs (so the diff doesn't churn from CLI ordering).
    out.sort()
    return out[:INCOMING_MAX]


# Match an existing `incoming:` block (block-style list) or single-line.
_INCOMING_BLOCK_RE = re.compile(
    r"(?m)^incoming:\s*(?:\n[ \t]+-\s+.*)*\n?"
)
_INCOMING_UPDATED_RE = re.compile(r"(?m)^incoming_updated:\s*.*\n?")


def apply_incoming_edit(text: str, body_start: int, incoming: list[str]) -> str:
    """Insert/replace the `incoming:` block in the file's frontmatter.

    Format written:
        incoming:
          - path1
          - path2
        incoming_updated: 2026-05-01

    If `incoming` is empty, REMOVE any existing block (signals the note is
    currently unreferenced — don't leave stale entries). Preserves all other
    frontmatter keys verbatim.
    """
    fm_text = text[:body_start]

    # Strip any existing incoming / incoming_updated blocks.
    fm_clean = _INCOMING_BLOCK_RE.sub("", fm_text)
    fm_clean = _INCOMING_UPDATED_RE.sub("", fm_clean)

    if not incoming:
        # Just leave the cleaned frontmatter (no incoming block).
        return fm_clean + text[body_start:]

    # Build the new block.
    block_lines = ["incoming:"]
    for p in incoming:
        block_lines.append(f"  - {p}")
    block_lines.append(f"incoming_updated: {date.today().isoformat()}")
    new_block = "\n".join(block_lines) + "\n"

    # Insert before the closing `---` line of the frontmatter.
    closing_match = re.search(r"\n---\s*\n", fm_clean)
    if closing_match is None:
        # malformed; bail
        return text
    insertion_point = closing_match.start() + 1   # right after the preceding \n
    new_fm = fm_clean[:insertion_point] + new_block + fm_clean[insertion_point:]
    return new_fm + text[body_start:]


# ---------------------------------------------------------------------------
# Parallel scan infrastructure
# ---------------------------------------------------------------------------

# Per-process globals — populated by _worker_init, used by _scan_one_file.
# With fork-based multiprocessing (Linux default), forked workers inherit these
# from the parent; the initializer is a belt-and-suspenders correctness guarantee
# and also makes the code work correctly with spawn-based pools (Windows/macOS).
_g_inventory: dict[str, Anchor] = {}
_g_tag_rules: dict[str, TagRule] = {}
_g_topic_summaries: dict[str, tuple[Path, str]] = {}
_g_existing_filenames: set[str] = set()
_g_kb_root: Path = Path()


def _worker_init(
    inventory: dict[str, Anchor],
    tag_rules: dict[str, TagRule],
    topic_summaries: dict[str, tuple[Path, str]],
    existing_filenames: set[str],
    kb_root_str: str,
) -> None:
    global _g_inventory, _g_tag_rules, _g_topic_summaries, _g_existing_filenames, _g_kb_root
    _g_inventory = inventory
    _g_tag_rules = tag_rules
    _g_topic_summaries = topic_summaries
    _g_existing_filenames = existing_filenames
    _g_kb_root = Path(kb_root_str)


def _scan_one_file(args: tuple) -> dict | None:
    """Picklable top-level worker: scan one .md file, return proposal dicts."""
    path_str, max_per_section, do_links, do_tags, do_bare_topics = args
    path = Path(path_str)
    rel = str(path.relative_to(_g_kb_root))
    try:
        mtime_ns = path.stat().st_mtime_ns
        text = path.read_text()
    except OSError:
        return None
    fm, body, body_start = split_frontmatter(text)
    own_slug = path.stem
    own_title = fm.get("title")
    frontmatter_line_offset = text.count("\n", 0, body_start)

    wikilink_props: list[WikilinkProposal] = []
    wikilink_skipped: list[dict] = []
    bare_topic_props: list[BareTopicProposal] = []
    tag_props: list[TagProposal] = []

    if do_links:
        wikilink_props, wikilink_skipped = scan_file_wikilinks(
            path, rel, body, frontmatter_line_offset, _g_inventory,
            own_slug, own_title, max_per_section,
        )

    if do_bare_topics and _g_topic_summaries:
        bare_topic_props = scan_file_bare_topics(
            rel, body, frontmatter_line_offset, own_slug,
            _g_topic_summaries, _g_existing_filenames,
        )

    if do_tags and _g_tag_rules:
        existing_anchors = extract_outgoing_anchors(body)
        proposed_anchors = {p.anchor_slug for p in wikilink_props}
        outgoing = existing_anchors | proposed_anchors
        existing_tags = extract_existing_tags(fm)
        tag_props = scan_file_tags(rel, body, own_slug, existing_tags, outgoing, _g_tag_rules)

    return {
        "rel": rel,
        "path": path_str,
        "mtime_ns": mtime_ns,
        "fm": fm,
        "body_start": body_start,
        "wikilink_props": wikilink_props,
        "wikilink_skipped": wikilink_skipped,
        "tag_props": tag_props,
        "bare_topic_props": bare_topic_props,
    }


def _print_human_summary(summary: dict) -> None:
    """Human-readable summary for TTY output."""
    print(f"\nKB Relink — scope: {summary['scope']}  mode: {summary['mode']}  passes: {summary['passes']}")
    print(f"  Files scanned:     {summary['files_scanned']}")
    print(f"  Files w/proposals: {summary['files_with_proposals']}")
    print(f"  Wikilinks added:   {summary['wikilink_proposals']}")
    print(f"  Tags added:        {summary['tag_proposals']}")
    print(f"  Bare topic fixes:  {summary['bare_topic_proposals']}")
    if summary.get("incoming_updates"):
        print(f"  Incoming updated:  {summary['incoming_updates']}")
    if summary["top_anchors"]:
        print("\nTop anchors by additions:")
        for a in summary["top_anchors"][:10]:
            print(f"  {a['count']:4d}  {a['anchor']}")
    if summary["top_tags"]:
        print("\nTop tags by additions:")
        for t in summary["top_tags"][:10]:
            print(f"  {t['count']:4d}  {t['tag']}")
    if summary["top_bare_topics"]:
        print("\nTop bare-topic fixes:")
        for b in summary["top_bare_topics"][:10]:
            print(f"  {b['count']:4d}  {b['topic']}")
    if summary["per_file"]:
        print(f"\nFiles with proposals ({len(summary['per_file'])}):")
        for r in summary["per_file"][:20]:
            wl = len(r["wikilinks"])
            tg = len(r["tags_added"])
            bt = len(r["bare_topics"])
            parts = []
            if wl: parts.append(f"+{wl} wikilinks")
            if tg: parts.append(f"+{tg} tags")
            if bt: parts.append(f"+{bt} bare-topics")
            print(f"  {r['path']}  ({', '.join(parts)})")
        if len(summary["per_file"]) > 20:
            print(f"  ... and {len(summary['per_file']) - 20} more")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scope", nargs="?", default=None,
                        help="Topic slug, relative path under KB root, or omit for full KB")
    parser.add_argument("--mode", choices=["dry-run", "apply", "report-only"], default="dry-run")
    parser.add_argument("--max-per-section", type=int, default=1)
    parser.add_argument("--report-path", default=None,
                        help="Write a Markdown report to this path (defaults to <KB>/_relink-report.md when --mode=report-only)")
    parser.add_argument("--kb-root", default=str(KB_ROOT_DEFAULT))
    parser.add_argument("--json", action="store_true",
                        help="Always emit JSON to stdout (default when stdout is not a TTY)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel worker processes for scanning (default: CPU count, max 8)")
    pass_group = parser.add_mutually_exclusive_group()
    pass_group.add_argument("--links-only", action="store_true",
                            help="Skip tag enrichment and bare-topic rewrite; only fix wikilinks")
    pass_group.add_argument("--tags-only", action="store_true",
                            help="Skip wikilink + bare-topic passes; only fix tags")
    pass_group.add_argument("--bare-topics-only", action="store_true",
                            help="Skip wikilink + tag passes; only rewrite bare-topic wikilinks")
    parser.add_argument("--rewrite-bare-topics", action="store_true",
                        help="Enable Pass 3: rewrite [[topic-slug]] -> [[topic-slug/_summary|<Display>]] "
                             "where the bare slug doesn't resolve to any file but topics/<slug>/_summary.md exists")
    parser.add_argument("--refresh-incoming", action="store_true",
                        help="Pass 4: snapshot each entity/concept/synthesis note's backlinks into "
                             "frontmatter `incoming: [...]`. Replaces any existing block. "
                             "Cheap on-the-read context (skips a runtime CLI call). Cap "
                             f"{INCOMING_MAX} paths per note. Implies use of `~/.local/bin/obsidian`.")
    args = parser.parse_args()

    kb_root = Path(args.kb_root)
    if not kb_root.is_dir():
        print(f"error: KB not found at {kb_root}", file=sys.stderr)
        return 2

    do_links = not (args.tags_only or args.bare_topics_only)
    do_tags = not (args.links_only or args.bare_topics_only)
    do_bare_topics = args.rewrite_bare_topics or args.bare_topics_only
    do_incoming = args.refresh_incoming
    if (args.links_only or args.tags_only) and args.rewrite_bare_topics:
        do_bare_topics = True

    emit_json = args.json or not sys.stdout.isatty()

    # --- Build shared inventories (single-threaded, once) ---
    print("Building anchor inventory...", end="", file=sys.stderr, flush=True)
    inventory = build_anchor_inventory(kb_root)
    print(f" {len(inventory)} anchors", file=sys.stderr)

    tag_rules = load_tag_rules() if do_tags else {}
    topic_summaries: dict[str, tuple[Path, str]] = {}
    existing_filenames: set[str] = set()
    if do_bare_topics:
        topic_summaries = build_topic_summary_inventory(kb_root)
        existing_filenames = build_existing_filename_set(kb_root)

    files = resolve_scope(kb_root, args.scope)
    print(f"Scanning {len(files)} files...", file=sys.stderr, flush=True)

    # --- Parallel scan phase ---
    n_workers = min(args.workers or os.cpu_count() or 4, 8)
    task_args = [
        (str(p), args.max_per_section, do_links, do_tags, do_bare_topics)
        for p in files
    ]
    raw_results: list[dict] = []

    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_worker_init,
        initargs=(inventory, tag_rules, topic_summaries, existing_filenames, str(kb_root)),
    ) as pool:
        future_to_path = {pool.submit(_scan_one_file, a): a[0] for a in task_args}
        completed = 0
        for future in as_completed(future_to_path):
            completed += 1
            name = Path(future_to_path[future]).name
            print(f"\r[{completed}/{len(files)}] {name:<55}", end="", file=sys.stderr, flush=True)
            result = future.result()
            if result is not None:
                raw_results.append(result)

    print(file=sys.stderr)  # end progress line

    # Sort for deterministic output order
    raw_results.sort(key=lambda r: r["rel"])

    # --- Aggregate stats from scan ---
    file_results_for_report: list[dict] = []
    total_wikilinks = 0
    total_tag_adds = 0
    total_bare_topics = 0
    anchor_counts: dict[str, int] = {}
    tag_counts: dict[str, dict] = {}
    bare_topic_counts: dict[str, int] = {}

    for res in raw_results:
        wl = res["wikilink_props"]
        tg = res["tag_props"]
        bt = res["bare_topic_props"]
        if not (wl or tg or bt or res["wikilink_skipped"]):
            continue
        file_results_for_report.append(res)
        total_wikilinks += len(wl)
        total_tag_adds += len(tg)
        total_bare_topics += len(bt)
        for p in wl:
            anchor_counts[p.anchor_slug] = anchor_counts.get(p.anchor_slug, 0) + 1
        for tp in tg:
            rec = tag_counts.setdefault(tp.tag, {"count": 0, "phrase": 0, "via_anchor": 0})
            rec["count"] += 1
            rec[tp.trigger_type] += 1
        for btp in bt:
            bare_topic_counts[btp.topic_slug] = bare_topic_counts.get(btp.topic_slug, 0) + 1

    # Step 7 safety checks
    if args.mode == "apply" and len(file_results_for_report) > 50:
        print(f"warn: {len(file_results_for_report)} files would be modified — "
              "large sweep; double-check scope before proceeding", file=sys.stderr)
    for res in raw_results:
        if len(res["wikilink_props"]) > 20:
            print(f"warn: {res['rel']} has {len(res['wikilink_props'])} wikilink proposals — "
                  "a phrase may be too generic", file=sys.stderr)

    # --- Apply edits (sequential, safe_write with mtime guard) ---
    total_incoming_updates = 0

    if args.mode == "apply":
        for res in raw_results:
            wl = res["wikilink_props"]
            tg = res["tag_props"]
            bt = res["bare_topic_props"]
            if not (wl or tg or bt):
                continue
            path = Path(res["path"])
            try:
                current_mtime = path.stat().st_mtime_ns
                text = path.read_text()
            except OSError:
                continue
            if current_mtime != res["mtime_ns"]:
                print(f"warn: {res['rel']} changed since scan; skipping", file=sys.stderr)
                continue
            fm, body, body_start = split_frontmatter(text)
            new_body = body

            if bt:
                # Apply bare-topic rewrites first, then re-scan for wikilinks on
                # the updated body so offsets are valid (fixes the two-pass issue).
                new_body = apply_bare_topic_edits(new_body, bt)
                if do_links:
                    own_slug = path.stem
                    own_title = fm.get("title")
                    frontmatter_line_offset = text.count("\n", 0, body_start)
                    fresh_wl, _ = scan_file_wikilinks(
                        path, res["rel"], new_body, frontmatter_line_offset, inventory,
                        own_slug, own_title, args.max_per_section,
                    )
                    if fresh_wl:
                        new_body = apply_wikilink_edits(new_body, fresh_wl)
            elif wl:
                new_body = apply_wikilink_edits(new_body, wl)

            new_text = text[:body_start] + new_body
            if tg:
                new_tags = [tp.tag for tp in tg]
                candidate = apply_tag_edits(new_text, body_start, new_tags)
                if verify_yaml_parses(candidate):
                    new_text = candidate
                else:
                    print(f"warn: tag edits would break YAML in {res['rel']}; reverted",
                          file=sys.stderr)

            if new_text != text:
                safe_write(path, new_text, current_mtime)

    # --- Pass 4: incoming-link snapshot (sequential, optional) ---
    if do_incoming:
        for path in files:
            rel = str(path.relative_to(kb_root))
            if not is_anchor_source(rel):
                continue
            try:
                mtime_ns = path.stat().st_mtime_ns
                text = path.read_text()
            except OSError:
                continue
            fm, body, body_start = split_frontmatter(text)
            if fm.get("type") not in ("entity", "concept", "synthesis"):
                continue
            slug = path.stem
            new_incoming = normalize_incoming_paths(query_backlinks(slug), rel)
            existing_incoming = fm.get("incoming") or []
            existing_paths = [str(p).strip() for p in existing_incoming if p] \
                if isinstance(existing_incoming, list) else []
            if new_incoming != existing_paths:
                total_incoming_updates += 1
                if args.mode == "apply":
                    candidate = apply_incoming_edit(text, body_start, new_incoming)
                    if verify_yaml_parses(candidate):
                        safe_write(path, candidate, mtime_ns)
                    else:
                        print(f"warn: incoming edit would break YAML in {rel}; reverted",
                              file=sys.stderr)

    # --- Build report ---
    pass_parts: list[str] = []
    if do_links: pass_parts.append("wikilinks")
    if do_tags: pass_parts.append("tags")
    if do_bare_topics: pass_parts.append("bare-topics")
    if do_incoming: pass_parts.append("incoming")
    passes = "+".join(pass_parts) if pass_parts else "none"

    summary = {
        "scope": args.scope or "(full KB)",
        "mode": args.mode,
        "passes": passes,
        "files_scanned": len(files),
        "files_with_proposals": len(file_results_for_report),
        "wikilink_proposals": total_wikilinks,
        "tag_proposals": total_tag_adds,
        "bare_topic_proposals": total_bare_topics,
        "incoming_updates": total_incoming_updates,
        "top_anchors": [
            {"anchor": a, "count": c}
            for a, c in sorted(anchor_counts.items(), key=lambda kv: -kv[1])[:10]
        ],
        "top_tags": [
            {"tag": t, "count": rec["count"], "phrase": rec["phrase"], "via_anchor": rec["via_anchor"]}
            for t, rec in sorted(tag_counts.items(), key=lambda kv: -kv[1]["count"])[:10]
        ],
        "top_bare_topics": [
            {"topic": t, "count": c}
            for t, c in sorted(bare_topic_counts.items(), key=lambda kv: -kv[1])[:10]
        ],
        "per_file": [
            {
                "path": r["rel"],
                "wikilinks": [p.to_dict() for p in r["wikilink_props"]],
                "tags_added": [p.to_dict() for p in r["tag_props"]],
                "bare_topics": [p.to_dict() for p in r["bare_topic_props"]],
            }
            for r in file_results_for_report
        ],
    }

    if emit_json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human_summary(summary)

    if args.mode == "report-only" and args.report_path is None:
        args.report_path = str(kb_root / "_relink-report.md")
    if args.report_path:
        write_markdown_report(args.report_path, summary)

    return 0


def write_markdown_report(path: str, summary: dict) -> None:
    lines: list[str] = []
    lines.append(f"# KB Relink Report\n")
    lines.append(f"Scope: `{summary['scope']}`\nMode: `{summary['mode']}`\n")
    lines.append(f"## Summary\n")
    lines.append(f"- Files scanned: {summary['files_scanned']}")
    lines.append(f"- Files with proposals: {summary['files_with_proposals']}")
    lines.append(f"- Wikilink proposals: {summary['wikilink_proposals']}\n")
    if summary["top_anchors"]:
        lines.append("## Top anchors by additions\n")
        lines.append("| Anchor | Additions |")
        lines.append("|---|---|")
        for a in summary["top_anchors"]:
            lines.append(f"| {a['anchor']} | {a['count']} |")
        lines.append("")
    lines.append("## Per-file proposals\n")
    for r in summary["per_file"]:
        if not r["wikilinks"]:
            continue
        lines.append(f"### {r['path']} (+{len(r['wikilinks'])})\n")
        for p in r["wikilinks"]:
            lines.append(f"- L{p['line']}: \"{p['phrase']}\" -> [[{p['anchor']}|{p['phrase']}]]")
        lines.append("")
    Path(path).write_text("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
