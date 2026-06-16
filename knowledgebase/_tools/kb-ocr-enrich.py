#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai>=0.3",
# ]
# ///
"""OCR + Vision Description enrichment for KB media-description notes.

For each media-description note in scope that has:
  - author: kb-bot
  - media_status: captured
  - A `*Pending.*`-marked ## OCR or ## Vision Description section

Run the requested enrichment stage:
  - --ocr-only   : Tesseract on local_path -> ## OCR
  - --vision-only: Gemini Flash on local_path -> ## Vision Description
  - --both       : (default) run both stages

Idempotent: skips a section if its content does not begin with `*Pending.*`.
Authorship guard: refuses to write to any note where frontmatter `author` != `kb-bot`.
Source-note immutability (Rule 11) does not apply — this script only writes to
`notes/media/*.md` files (kb-bot-authored, mutable per Rule 29 enrichment-rule).

Usage:
  # Pilot: 3 notes, OCR only
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --limit 3

  # Full topic, OCR only (no Gemini key required)
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only

  # Full topic, vision only (needs GEMINI_API_KEY env var)
  kb-ocr-enrich.py --topic earthquake-prediction --vision-only

  # Single note
  kb-ocr-enrich.py --note topics/earthquake-prediction/notes/media/foo.md

  # Dry run (no writes)
  kb-ocr-enrich.py --topic earthquake-prediction --ocr-only --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Free-tier Gemini limit on gemini-2.5-flash is ~5 requests/minute in practice
# (sometimes lower under sliding-window scrutiny). Sleep this long between successful
# Vision calls so a full batch does not hit 429s.
GEMINI_RATE_LIMIT_SLEEP_SECONDS = 20
# On 429, parse the retry-after hint and back off this many extra seconds (margin).
GEMINI_429_BACKOFF_MARGIN_SECONDS = 3

# Active Gemini model. Each model has its own daily free-tier quota (~20 RPD per model
# on this account tier in practice — much lower than docs suggest). When one model hits
# its daily quota, swap by uncommenting a different line and commenting this one out.
MODEL_NAME = "gemini-2.5-flash"              # active: default, best quality/quota balance for vision
# MODEL_NAME = "gemini-2.5-flash-lite"       # cheaper/faster, lower description quality, separate quota pool
# MODEL_NAME = "gemini-2.0-flash"            # 0 RPD on this account tier — unavailable as of 2026-05-18
# MODEL_NAME = "gemini-2.5-pro"              # highest quality, very tight free-tier quota
# MODEL_NAME = "gemini-2.5-pro"              # highest quality, very tight free-tier quota

PENDING_RE = re.compile(r"^\s*\*?Pending\b", re.MULTILINE | re.IGNORECASE)
SECTION_RE = re.compile(r"^(## [^\n]+)\n", re.MULTILINE)

GEMINI_VISION_PROMPT = """You are describing an image from a knowledge-base media-description note. Write a precise, two-paragraph Vision Description suitable for an analytical wiki note.

Paragraph 1 — structural description:
- What kind of image is it? (chart, diagram, photograph, screenshot, map, table)
- For charts: chart type (bar, scatter, line, histogram, etc.), what's on each axis, units, scale
- For diagrams: components, layout, what is connected to what
- For photographs: what is depicted, setting, framing
- For screenshots: of what (website, paper figure, social media post), key visible text

Paragraph 2 — analytical observations:
- What visual claim does the image make?
- What is the most prominent feature?
- Any labels, captions, embedded text relevant to interpretation
- Any anomalies, missing labels, or layout choices worth noting

Tone: neutral and descriptive. Do not editorialize about whether the image is correct or misleading; just describe what is visually present and what the visual claim is. Do not exceed two paragraphs. Do not use bullet points.
"""


# ----- frontmatter parsing -----


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """Returns (frontmatter_dict, body) or None if no frontmatter."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    fm_text = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, Any] = {}
    current_list: list[str] | None = None
    for line in fm_text.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is not None:
                v = line.strip()[2:].strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                current_list.append(v)
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                current_list = []
                fm[key] = current_list
            elif value.startswith("[") and value.endswith("]"):
                items = [x.strip().strip('"') for x in value[1:-1].split(",") if x.strip()]
                fm[key] = items
                current_list = None
            else:
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                fm[key] = value
                current_list = None
    return fm, body


# ----- section replacement -----


def find_section(body: str, heading: str) -> tuple[int, int] | None:
    """Return (content_start, content_end) byte offsets in body for the named section.
    content_start is the byte just after the heading's trailing newline.
    content_end is the byte just before the next `## ` heading or the EOF.
    Returns None if heading not found.
    """
    pattern = re.compile(r"^## " + re.escape(heading) + r"\s*\n", re.MULTILINE)
    m = pattern.search(body)
    if not m:
        return None
    start = m.end()
    next_section = SECTION_RE.search(body, start)
    end = next_section.start() if next_section else len(body)
    return start, end


def section_content(body: str, heading: str) -> str | None:
    span = find_section(body, heading)
    if not span:
        return None
    return body[span[0] : span[1]]


def replace_section(body: str, heading: str, new_content: str) -> str:
    """Replace the named section's content. Adds a blank line separator after content."""
    span = find_section(body, heading)
    if not span:
        return body
    start, end = span
    # Ensure new content ends with a single trailing newline and a blank line before next section
    new_content = new_content.rstrip("\n") + "\n\n"
    return body[:start] + new_content + body[end:]


def is_pending(content: str) -> bool:
    """True if the section content begins with a `Pending` marker (italic or bare)."""
    return bool(PENDING_RE.match(content.strip())) if content else True


def normalize_combined_section(body: str) -> str:
    """Split a combined `## OCR / Vision Description` section into two separate sections.

    Some media-description notes were written with the combined format (one heading,
    one `*Both pending.*` marker). This helper rewrites them into two sections so the
    rest of the pipeline (which operates on separate sections) can apply.

    Preserves any contextual prose by attaching it to the Vision Description section.
    """
    combined_re = re.compile(r"^## OCR / Vision Description\s*\n", re.MULTILINE)
    m = combined_re.search(body)
    if not m:
        return body
    start = m.end()
    next_section = SECTION_RE.search(body, start)
    end = next_section.start() if next_section else len(body)
    existing = body[start:end].strip()
    # Strip leading "*Both pending.*" or similar markers; keep contextual prose
    existing = re.sub(r"^\*?\s*Both\s+pending\.?\s*\*?\s*", "", existing, flags=re.IGNORECASE).strip()
    if existing:
        vision_placeholder = f"*Pending.* {existing}\n\n"
    else:
        vision_placeholder = "*Pending.*\n\n"
    replacement = (
        "## OCR\n\n"
        "*Pending.*\n\n"
        "## Vision Description\n\n"
        + vision_placeholder
    )
    return body[: m.start()] + replacement + body[end:]


# ----- OCR stage -----


def run_tesseract(image_path: Path) -> str:
    """Run tesseract; return cleaned OCR text. Raises on failure."""
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not installed (apt install tesseract-ocr)")
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    result = subprocess.run(
        ["tesseract", str(image_path), "-", "-l", "eng", "--psm", "6"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tesseract exited {result.returncode}: {result.stderr.strip()}")
    text = result.stdout.strip()
    # Collapse multiple blank lines, normalise whitespace
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Drop runs of blank lines at the boundaries
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Collapse internal triple+ blanks to single blank
    cleaned: list[str] = []
    prev_blank = False
    for ln in lines:
        if not ln.strip():
            if prev_blank:
                continue
            prev_blank = True
            cleaned.append("")
        else:
            prev_blank = False
            cleaned.append(ln)
    return "\n".join(cleaned)


def format_ocr_section(ocr_text: str) -> str:
    """Wrap OCR text in a code block per KB convention; flag empties explicitly."""
    if not ocr_text.strip():
        return "*OCR ran but extracted no text — likely a pure-graphic image with no machine-readable labels.*\n"
    return "```text\n" + ocr_text + "\n```\n"


# ----- Vision stage -----


def run_gemini_vision(image_path: Path) -> str:
    """Call Gemini Flash with the image + prompt; return description text.

    Uses the current `google-genai` SDK (the deprecated `google-generativeai` package
    has been replaced upstream).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable not set")
    # Lazy import so --ocr-only does not require the dep
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=api_key)
    image_bytes = image_path.read_bytes()
    # Detect MIME from suffix
    suffix = image_path.suffix.lower().lstrip(".")
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
    mime = mime_map.get(suffix, "image/png")

    def _call() -> Any:
        return client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                GEMINI_VISION_PROMPT,
            ],
        )

    # Retry once on 429 with the API's suggested retry-after, plus a small margin.
    try:
        response = _call()
    except Exception as e:
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            # Try to parse "Please retry in NN.NNNs"
            m = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", msg)
            wait = float(m.group(1)) if m else 30.0
            wait += GEMINI_429_BACKOFF_MARGIN_SECONDS
            time.sleep(wait)
            response = _call()
        else:
            raise
    return response.text.strip()


# ----- enrichment driver -----


def enrich_note(
    note_path: Path,
    kb_root: Path,
    do_ocr: bool,
    do_vision: bool,
    dry_run: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "note": str(note_path.relative_to(kb_root)),
        "ocr": None,
        "vision": None,
        "skipped": False,
        "error": None,
    }

    text = note_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_frontmatter(text)
    if not parsed:
        result["error"] = "no frontmatter"
        return result
    fm, body = parsed

    if fm.get("author") != "kb-bot":
        result["error"] = "authorship guard: author != kb-bot"
        return result
    if fm.get("type") != "media-description":
        result["error"] = "not a media-description note"
        return result
    if fm.get("media_status") != "captured":
        result["skipped"] = True
        result["error"] = f"media_status is {fm.get('media_status')!r}, expected 'captured'"
        return result

    local_path_raw = fm.get("local_path")
    if not local_path_raw:
        result["error"] = "no local_path in frontmatter"
        return result
    # local_path is vault-rooted (knowledgebase/_media/...); resolve relative to KB root's parent
    if local_path_raw.startswith("knowledgebase/"):
        image_path = kb_root.parent / local_path_raw
    else:
        image_path = kb_root / local_path_raw
    if not image_path.exists():
        result["error"] = f"image file not found: {image_path}"
        return result

    new_body = normalize_combined_section(body)

    if do_ocr:
        ocr_section = section_content(new_body, "OCR")
        if ocr_section is None:
            result["ocr"] = "no ## OCR section"
        elif not is_pending(ocr_section):
            result["ocr"] = "already enriched (skipped)"
        else:
            try:
                ocr_text = run_tesseract(image_path)
                ocr_md = format_ocr_section(ocr_text)
                new_body = replace_section(new_body, "OCR", ocr_md)
                result["ocr"] = f"ok ({len(ocr_text)} chars)"
            except Exception as e:
                result["ocr"] = f"error: {e}"

    if do_vision:
        vis_section = section_content(new_body, "Vision Description")
        if vis_section is None:
            result["vision"] = "no ## Vision Description section"
        elif not is_pending(vis_section):
            result["vision"] = "already enriched (skipped)"
        else:
            try:
                vis_text = run_gemini_vision(image_path)
                new_body = replace_section(new_body, "Vision Description", vis_text + "\n")
                result["vision"] = f"ok ({len(vis_text)} chars)"
            except Exception as e:
                result["vision"] = f"error: {e}"

    if new_body != body and not dry_run:
        # Rebuild the full file: frontmatter + body
        # Reconstruct frontmatter from the original text (do not re-serialize fm dict;
        # preserve formatting and comments by splicing the original frontmatter block)
        fm_end = text.find("\n---\n", 4) + 5
        frontmatter_text = text[:fm_end]
        note_path.write_text(frontmatter_text + new_body, encoding="utf-8")
        result["written"] = True
    else:
        result["written"] = False

    return result


def find_media_notes(kb_root: Path, topic: str | None, single: Path | None) -> list[Path]:
    if single:
        return [single]
    if topic:
        scopes = [kb_root / "topics" / topic / "notes" / "media"]
    else:
        scopes = [
            p / "notes" / "media"
            for p in (kb_root / "topics").iterdir()
            if (p / "notes" / "media").is_dir()
        ]
    out: list[Path] = []
    for s in scopes:
        if s.is_dir():
            out.extend(sorted(s.glob("*.md")))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kb-root", default="~/Vault/knowledgebase")
    p.add_argument("--topic", help="Limit to a single topic (slug)")
    p.add_argument("--note", help="Run on a single media-description note path (relative to kb-root or absolute)")
    p.add_argument("--limit", type=int, default=0, help="Process at most N notes (0 = unlimited)")
    p.add_argument("--ocr-only", action="store_true")
    p.add_argument("--vision-only", action="store_true")
    p.add_argument("--both", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Run pipeline; do not write files")
    args = p.parse_args()

    if args.ocr_only and args.vision_only:
        p.error("--ocr-only and --vision-only are mutually exclusive")
    do_ocr = args.ocr_only or args.both or not (args.ocr_only or args.vision_only or args.both)
    do_vision = args.vision_only or args.both or not (args.ocr_only or args.vision_only or args.both)
    if args.ocr_only:
        do_vision = False
    if args.vision_only:
        do_ocr = False

    kb_root = Path(args.kb_root).resolve()

    single: Path | None = None
    if args.note:
        single = Path(args.note)
        if not single.is_absolute():
            single = kb_root / single
        if not single.exists():
            print(json.dumps({"error": f"note not found: {single}"}), file=sys.stderr)
            return 1

    notes = find_media_notes(kb_root, args.topic, single)
    if args.limit > 0:
        notes = notes[: args.limit]

    results = []
    for i, n in enumerate(notes):
        r = enrich_note(n, kb_root, do_ocr=do_ocr, do_vision=do_vision, dry_run=args.dry_run)
        results.append(r)
        # Rate-limit Gemini calls (free-tier is 5 RPM). Only sleep when a vision call
        # was actually made (skipped notes don't count against quota) and only if more
        # notes remain in the batch.
        if (
            do_vision
            and (r.get("vision") or "").startswith("ok")
            and i < len(notes) - 1
        ):
            time.sleep(GEMINI_RATE_LIMIT_SLEEP_SECONDS)

    summary = {
        "kb_root": str(kb_root),
        "stages": {"ocr": do_ocr, "vision": do_vision},
        "dry_run": args.dry_run,
        "processed": len(results),
        "written": sum(1 for r in results if r.get("written")),
        "ocr_ok": sum(1 for r in results if (r.get("ocr") or "").startswith("ok")),
        "vision_ok": sum(1 for r in results if (r.get("vision") or "").startswith("ok")),
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
