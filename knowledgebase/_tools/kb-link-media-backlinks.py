#!/usr/bin/env python3
"""Suggest (and optionally apply) See Also backlinks from mutable concept/synthesis notes
to media-description notes whose parent source note is immutable per source preservation contract.

Problem this addresses:
When media is retro-captured after its parent source note was written, source preservation contract freezes
the source-note `media:` block — the source cannot be updated to point at the new
media-description note. The media-description note therefore has no incoming wikilinks
from any other content note in the KB, and surfaces as an orphan during lint.

Workflow integration:
Called from kb-ingest and the internal kb-auto worker's media phase, immediately after
writing the media-description note. The script identifies mutable kb-bot-authored
concept / entity / synthesis notes that share thematic anchors with the new media note
(shared source citations or shared related: entries), ranks them, and outputs
suggested See Also entries. With --apply, appends those entries directly (article maintenance
append-only) with the kb-bot authorship guard.

Usage:
    # Suggest backlinks for one media-desc note (default: top 3)
    kb-link-media-backlinks.py --media-note topics/foo/notes/media/bar.md

    # Find every orphan media-desc note in a topic and suggest backlinks for each
    kb-link-media-backlinks.py --topic earthquake-prediction --all-orphans

    # Apply the suggestions (kb-bot guard enforced)
    kb-link-media-backlinks.py --media-note ... --apply

    # Larger candidate set
    kb-link-media-backlinks.py --media-note ... --top 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
MUTABLE_NOTE_KINDS = ("concepts", "entities", "syntheses")  # NOT sources (source preservation contract), NOT media (self-reference)


def rel(path: Path, kb_root: Path) -> str:
    """Path relative to KB root, e.g. 'topics/foo/notes/bar.md'."""
    return path.relative_to(kb_root).as_posix()


def vault_link(path: Path, kb_root: Path) -> str:
    """Vault-rooted wikilink target form, e.g. 'knowledgebase/topics/foo/notes/bar'.
    Wikilinks in the KB always include the 'knowledgebase/' prefix per link format.
    """
    r = path.relative_to(kb_root).as_posix()
    if r.endswith(".md"):
        r = r[:-3]
    return "knowledgebase/" + r


def normalize_link(target: str) -> str:
    """Normalize a wikilink target to the canonical form for comparison.
    Strips trailing .md if present; leaves the knowledgebase/ prefix in place.
    """
    if target.endswith(".md"):
        target = target[:-3]
    return target


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Minimal YAML frontmatter parser — handles the subset kb-bot produces."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    fm_text = text[4:end]
    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in fm_text.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            # list item (after a `key:` line on previous iteration)
            if current_list is not None:
                value = line.strip()[2:].strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                current_list.append(value)
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "" or value == "[]":
                # opens a list
                current_list = []
                fm[key] = current_list
                current_key = key
            elif value.startswith("[") and value.endswith("]"):
                # inline list (rare in this KB)
                items = [v.strip().strip('"') for v in value[1:-1].split(",") if v.strip()]
                fm[key] = items
                current_list = None
                current_key = None
            else:
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                fm[key] = value
                current_list = None
                current_key = None
    return fm


def extract_wikilink_targets(values: list[str] | str | None) -> set[str]:
    """Extract bare path strings from a frontmatter field that may contain wikilinks
    like [[knowledgebase/topics/foo/sources/bar]]."""
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    out = set()
    for v in values:
        for m in WIKILINK_RE.finditer(v):
            out.add(m.group(1))
        # also handle bare-path strings (no wikilink syntax)
        bare = v.strip()
        if bare and not bare.startswith("[["):
            out.add(bare)
    return out


def find_orphan_media_notes(kb_root: Path, topic: str | None) -> list[Path]:
    """A media-desc note is treated as orphan-candidate if no mutable note links to it."""
    if topic:
        scopes = [kb_root / "topics" / topic]
    else:
        scopes = [p for p in (kb_root / "topics").iterdir() if p.is_dir()]

    # Collect all media-desc notes
    media_notes: list[Path] = []
    for scope in scopes:
        media_dir = scope / "notes" / "media"
        if media_dir.is_dir():
            media_notes.extend(media_dir.glob("*.md"))

    # Build wikilink incoming-map from all mutable notes (concepts/entities/syntheses)
    # Keys are vault-link form: 'knowledgebase/topics/.../notes/.../slug'
    incoming: dict[str, int] = {}
    for scope in scopes:
        for kind in MUTABLE_NOTE_KINDS:
            d = scope / "notes" / kind
            if not d.is_dir():
                continue
            for f in d.glob("*.md"):
                text = f.read_text(encoding="utf-8", errors="replace")
                for m in WIKILINK_RE.finditer(text):
                    target = normalize_link(m.group(1))
                    incoming[target] = incoming.get(target, 0) + 1

    orphans: list[Path] = []
    for mn in media_notes:
        target_key = vault_link(mn, kb_root)
        if incoming.get(target_key, 0) == 0:
            orphans.append(mn)
    return orphans


def find_backlink_candidates(
    media_note: Path, kb_root: Path, top: int = 3
) -> list[dict[str, Any]]:
    """For a given media-desc note, return ranked candidate mutable notes for backlinks."""
    text = media_note.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)
    if not fm:
        return []
    if fm.get("type") != "media-description":
        return []
    if fm.get("author") != "kb-bot":
        return []

    media_sources = extract_wikilink_targets(fm.get("sources"))
    media_related = extract_wikilink_targets(fm.get("related"))
    media_topic = fm.get("topic")
    if not media_topic:
        return []

    # Determine the canonical wikilink path of this media-desc note (vault-link form)
    media_target = vault_link(media_note, kb_root)

    # Scan all mutable notes in the same topic
    topic_dir = kb_root / "topics" / media_topic
    candidates: list[dict[str, Any]] = []
    for kind in MUTABLE_NOTE_KINDS:
        d = topic_dir / "notes" / kind
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            ftext = f.read_text(encoding="utf-8", errors="replace")
            ffm = parse_frontmatter(ftext)
            if not ffm:
                continue
            if ffm.get("author") != "kb-bot":
                continue

            # If this note already backlinks to our media note, it is not a candidate
            f_targets = set()
            for m in WIKILINK_RE.finditer(ftext):
                f_targets.add(normalize_link(m.group(1)))
            if media_target in f_targets:
                continue

            # Score by shared source references and shared related: entries
            f_sources = extract_wikilink_targets(ffm.get("sources"))
            f_related = extract_wikilink_targets(ffm.get("related"))

            shared_sources = media_sources & f_sources
            shared_related = media_related & f_related

            # Note: a concept whose `sources:` includes the media-desc's `sources:` source
            # is thematically tied. A concept that lists the media-desc's `related:` items
            # also signals overlap.
            score = 2 * len(shared_sources) + len(shared_related)
            if score == 0:
                continue

            candidates.append(
                {
                    "path": rel(f, kb_root),
                    "title": ffm.get("title", f.stem),
                    "kind": kind,
                    "score": score,
                    "shared_sources": sorted(shared_sources),
                    "shared_related": sorted(shared_related),
                }
            )

    candidates.sort(key=lambda c: (-c["score"], c["path"]))
    return candidates[:top]


def append_see_also_entry(
    target_file: Path, media_target: str, media_title: str, reason: str
) -> bool:
    """Append a wikilink to the target file's See Also section. Returns True if modified.
    Authorship guard: target must be kb-bot authored.

    Idempotent: if a wikilink to media_target is already present anywhere in the file,
    do nothing.
    """
    text = target_file.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)
    if not fm or fm.get("author") != "kb-bot":
        return False

    # Idempotency check
    for m in WIKILINK_RE.finditer(text):
        if m.group(1).rstrip(".md") == media_target:
            return False

    bullet = f"- [[{media_target}|{media_title}]] — {reason}"

    if "## See Also" in text:
        # append at end of the See Also section (= end of file if it's the last section,
        # otherwise just before the next `## ` heading)
        idx = text.find("## See Also")
        next_h2 = text.find("\n## ", idx + 1)
        if next_h2 < 0:
            # last section — append at end of file
            if not text.endswith("\n"):
                text += "\n"
            text += bullet + "\n"
        else:
            before = text[:next_h2]
            after = text[next_h2:]
            if not before.endswith("\n"):
                before += "\n"
            text = before + bullet + "\n" + after
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n## See Also\n\n" + bullet + "\n"

    target_file.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument(
        "--media-note",
        help="Path to a specific media-description note (relative to kb-root or absolute)",
    )
    p.add_argument("--topic", help="Topic slug to scope --all-orphans search")
    p.add_argument(
        "--all-orphans",
        action="store_true",
        help="Find every orphan media-desc note in scope and suggest backlinks for each",
    )
    p.add_argument("--top", type=int, default=3, help="Max candidate backlinks per media note")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually append the suggested See Also entries (article maintenance append-only, kb-bot guard)",
    )
    args = p.parse_args()

    kb_root = Path(args.kb_root).resolve()

    targets: list[Path] = []
    if args.media_note:
        mn = Path(args.media_note)
        if not mn.is_absolute():
            mn = kb_root / mn
        if not mn.exists():
            print(json.dumps({"error": f"media note not found: {mn}"}), file=sys.stderr)
            return 1
        targets = [mn]
    elif args.all_orphans:
        targets = find_orphan_media_notes(kb_root, args.topic)
    else:
        p.error("must supply --media-note or --all-orphans")
        return 2

    results: list[dict[str, Any]] = []
    for mn in targets:
        text = mn.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text) or {}
        title = fm.get("title", mn.stem)
        media_target = vault_link(mn, kb_root)

        candidates = find_backlink_candidates(mn, kb_root, top=args.top)

        applied: list[str] = []
        if args.apply and candidates:
            for c in candidates:
                target_file = kb_root / c["path"]
                # Compose a short reason from the shared anchors
                shared = c["shared_sources"] or c["shared_related"]
                if shared:
                    anchor_short = shared[0].split("/")[-1].replace("-", " ")
                    reason = f"shared anchor: {anchor_short}"
                else:
                    reason = "thematic overlap"
                ok = append_see_also_entry(target_file, media_target, title, reason)
                if ok:
                    applied.append(c["path"])

        results.append(
            {
                "media_note": rel(mn, kb_root),
                "title": title,
                "candidates": candidates,
                "applied": applied if args.apply else None,
            }
        )

    print(json.dumps({"results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
