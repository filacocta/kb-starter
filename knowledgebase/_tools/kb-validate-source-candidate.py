#!/usr/bin/env python3
"""Pre-write validation for KB source/media candidate notes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


EVIDENCE_QUALITY_VALUES = frozenset(
    {
        "transcript-plus-visual",
        "transcript-only",
        "visual-only",
        "summary-only",
        "insufficient",
    }
)
VALID_INGEST_STATUS = {"succeeded", "failed"}
MEDIA_KINDS = {"image", "video", "audio"}
MEDIA_STATUSES = {"captured", "discovered", "not-captured"}
MEDIA_LICENSES = {
    "public-domain",
    "cc-by",
    "cc-by-sa",
    "cc0",
    "quoted-with-attribution",
    "unknown",
    "do-not-redistribute",
}
WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
EVIDENCE_QUALITY_RE = re.compile(r"^\s*(?:[-*]\s*)?evidence_quality\s*:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
SOURCE_COMMENTARY_HEADING_RE = re.compile(
    r"^\s*##+\s+(?:KB Context|Analysis|Agent Commentary)\s*$",
    flags=re.IGNORECASE,
)
SOURCE_AGENT_INFERENCE_RE = re.compile(r"\bagent inference\b", flags=re.IGNORECASE)
GENERIC_SOCIAL_TITLE_RE = re.compile(r"^@?[^—]+\s+—\s+(?:health-)?related claim record\s*\(\d+\)$", flags=re.IGNORECASE)
GENERIC_SOCIAL_DESCRIPTION_RE = re.compile(r"^a captured social-media source record\b", flags=re.IGNORECASE)
SOURCE_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", flags=re.IGNORECASE | re.MULTILINE)
DENSE_SOURCE_TYPE_RE = re.compile(
    # Do not treat transport wording such as "FxTwitter mirror transcript" as
    # a dense source. The gate applies to material with multiple substantive
    # units to cover, not every single social-post transcript.
    r"^\*\*Source type:\*\*.*\b(?:article|book|paper|podcast|report|thread|video)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)
RECOMMENDED_FIELDS = ("tags", "related", "sources")
_KB_SEEN_MODULE = None
HEALTH_EVIDENCE_BOUNDARY_RE = re.compile(
    r"\bnot\b.*\b(?:clinical|medical|treat(?:ment)?|diagnos(?:is|tic)|recommend(?:ation)?|guidance|protocol|dos(?:e|ing)|causal|safe(?:ty)?)\b",
    flags=re.IGNORECASE | re.DOTALL,
)
# Source notes preserve retrieved material plus an explicit evidence boundary.
# Broader KB analysis belongs in mutable concept/synthesis notes; placing it in
# a source record makes a later provenance correction impossible under source preservation contract.


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str
    line: int | None = None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    # Source candidates routinely carry nested `media:` lists, quoted URLs,
    # and quoted titles that end in apostrophes.  A line-splitting parser loses
    # that structure and can silently corrupt title/origin comparisons.
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, body
    if not isinstance(parsed, dict):
        return {}, body
    data: dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            continue
        # Preserve nested structures (especially media), but normalize scalar
        # YAML dates and numbers for the existing frontmatter checks.
        data[key] = value if isinstance(value, (dict, list)) else "" if value is None else str(value)
    return data, body


def load_kb_seen():
    """Load the shared URL identity implementation without import-path hacks."""
    global _KB_SEEN_MODULE
    if _KB_SEEN_MODULE is None:
        script = Path(__file__).with_name("kb-seen.py")
        spec = importlib.util.spec_from_file_location("kb_validate_shared_seen", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _KB_SEEN_MODULE = module
    return _KB_SEEN_MODULE


def add(findings: list[Finding], severity: str, code: str, path: str, message: str, line: int | None = None) -> None:
    findings.append(Finding(severity=severity, code=code, path=path, message=message, line=line))


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_user_owned_exception(path: Path, kb_root: Path) -> bool:
    try:
        parts = path.relative_to(kb_root).parts
    except ValueError:
        return False
    return bool(parts and parts[0] in {"clippings", "readinglist"})


def is_source_note_path(path: Path, kb_root: Path) -> bool:
    """Return whether *path* is in the immutable source-note directory."""
    try:
        parts = path.relative_to(kb_root).parts
    except ValueError:
        return False
    return len(parts) >= 4 and parts[0] == "topics" and parts[2] == "sources" and path.suffix == ".md"


def is_inside_code_fence_toggle(line: str, in_fence: bool) -> bool:
    if line.lstrip().startswith("```"):
        return not in_fence
    return in_fence


def strip_inline_code(line: str) -> str:
    out: list[str] = []
    in_code = False
    i = 0
    while i < len(line):
        if line[i] == "`":
            in_code = not in_code
            out.append(" ")
        elif not in_code:
            out.append(line[i])
        else:
            out.append(" ")
        i += 1
    return "".join(out)


def wikilinks_outside_code(text: str) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        next_fence = is_inside_code_fence_toggle(line, in_fence)
        if line.lstrip().startswith("```"):
            in_fence = next_fence
            continue
        if in_fence:
            continue
        for match in WIKILINK_RE.finditer(strip_inline_code(line)):
            links.append((lineno, match.group(1).strip()))
    return links


def evidence_quality_values(text: str) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        next_fence = is_inside_code_fence_toggle(line, in_fence)
        if line.lstrip().startswith("```"):
            in_fence = next_fence
            continue
        if in_fence:
            continue
        match = EVIDENCE_QUALITY_RE.match(strip_inline_code(line))
        if match:
            values.append((lineno, match.group(1)))
    return values


def find_existing_origin(kb_root: Path, origin: str, candidate_path: Path) -> Path | None:
    if not origin:
        return None
    topics_root = kb_root / "topics"
    if not topics_root.exists():
        return None
    kb_seen = load_kb_seen()
    for path in topics_root.glob("*/sources/*.md"):
        if path.resolve() == candidate_path.resolve():
            continue
        try:
            fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        if fm.get("origin") == origin:
            return path
        existing_origin = str(fm.get("origin", ""))
        if origin.startswith(("http://", "https://")) and existing_origin.startswith(("http://", "https://")):
            if kb_seen.normalize(origin) == kb_seen.normalize(existing_origin):
                return path
    return None


def find_alias_collision(kb_root: Path, origin: str, candidate_path: Path) -> Path | None:
    """Return the successful source targeted by a governed alias origin."""
    if not origin.startswith(("http://", "https://")):
        return None
    kb_seen = load_kb_seen()
    target = kb_seen.alias_source_for_url(origin, kb_root)
    if target is None:
        return None
    try:
        candidate_relative = candidate_path.resolve(strict=False).relative_to(kb_root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        candidate_relative = ""
    if target == candidate_relative:
        return None
    return kb_root / Path(*target.split("/"))


def source_section(body: str, heading: str) -> tuple[int, str] | None:
    """Return the first level-two section with this exact heading and its text."""
    matches = list(SOURCE_SECTION_RE.finditer(body))
    for index, match in enumerate(matches):
        if match.group(1).strip().casefold() != heading.casefold():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        line = body[: match.start()].count("\n") + 1
        return line, body[match.end() : end].strip()
    return None


def meaningful_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text))


def nonempty_value(value: Any) -> bool:
    """Accept YAML scalar dates/numbers while rejecting empty/null fields."""
    return value is not None and bool(str(value).strip())


def validate_source_media(media: Any, findings: list[Finding], display_path: str) -> None:
    """Validate immutable source-media blocks before source creation."""
    if media == []:
        return
    if media is None:
        add(
            findings,
            "block",
            "missing-source-media-manifest",
            display_path,
            "source candidates require an explicit media list; use 'media: []' when no media was discovered",
        )
        return
    if not isinstance(media, list):
        add(findings, "block", "invalid-source-media", display_path, "source media must be a YAML list")
        return

    seen_ids: dict[str, int] = {}
    for index, item in enumerate(media, 1):
        prefix = f"media item {index}"
        if not isinstance(item, dict):
            add(findings, "block", "invalid-source-media-item", display_path, f"{prefix} must be a mapping")
            continue
        missing = [field for field in ("id", "kind", "status", "origin_url", "license") if not nonempty_value(item.get(field))]
        if missing:
            add(
                findings,
                "block",
                "source-media-required-field",
                display_path,
                f"{prefix} missing required field(s): {', '.join(missing)}",
            )
            continue

        media_id = str(item["id"])
        if media_id in seen_ids:
            add(
                findings,
                "block",
                "duplicate-source-media-id",
                display_path,
                f"{prefix} repeats media id {media_id!r} from media item {seen_ids[media_id]}; consolidate byte-identical media before writing the immutable manifest",
            )
        else:
            seen_ids[media_id] = index

        kind = str(item["kind"])
        status = str(item["status"])
        license_value = str(item["license"])
        if kind not in MEDIA_KINDS:
            add(findings, "block", "invalid-source-media-kind", display_path, f"{prefix} kind must be one of {sorted(MEDIA_KINDS)}")
        if status not in MEDIA_STATUSES:
            add(findings, "block", "invalid-source-media-status", display_path, f"{prefix} status must be one of {sorted(MEDIA_STATUSES)}")
        if license_value not in MEDIA_LICENSES:
            add(findings, "block", "invalid-source-media-license", display_path, f"{prefix} license must be one of {sorted(MEDIA_LICENSES)}")
        origin_url = str(item["origin_url"])
        if urlparse(origin_url).scheme not in {"http", "https"}:
            add(findings, "block", "invalid-source-media-origin", display_path, f"{prefix} origin_url must be an http(s) URL")

        if status == "captured":
            missing_captured = [field for field in ("path", "captured", "description_note") if not nonempty_value(item.get(field))]
            if missing_captured:
                add(
                    findings,
                    "block",
                    "captured-source-media-incomplete",
                    display_path,
                    f"{prefix} captured media missing: {', '.join(missing_captured)}",
                )
            elif not str(item["path"]).startswith("knowledgebase/_media/"):
                add(
                    findings,
                    "block",
                    "invalid-source-media-path",
                    display_path,
                    f"{prefix} path must be vault-relative under knowledgebase/_media/",
                )
            elif not re.fullmatch(r"\[\[knowledgebase/topics/.+\]\]", str(item["description_note"]).strip()):
                add(
                    findings,
                    "block",
                    "invalid-source-media-description-note",
                    display_path,
                    f"{prefix} description_note must be a vault-rooted media-description wikilink",
                )
        elif status == "not-captured" and not nonempty_value(item.get("not_captured_reason")):
            add(
                findings,
                "block",
                "not-captured-media-missing-reason",
                display_path,
                f"{prefix} not-captured media requires not_captured_reason",
            )


def validate_staged_target(candidate_path: Path, resolved: Path, kb_root: Path, display_path: str) -> list[Finding]:
    """Check the on-disk logical destination before validating staged text.

    Staged text is not authoritative for ownership.  If the logical
    destination already exists, its current frontmatter controls whether a
    write is allowed.  Source-note bodies are immutable even when the
    existing note was created by kb-bot.
    """
    findings: list[Finding] = []

    # A final symlink inside the KB is still unsafe for a staged write: the
    # caller may follow it instead of creating the requested logical note.
    # Symlink escapes are rejected by the resolved containment check before
    # this helper is called.
    if candidate_path.is_symlink():
        add(
            findings,
            "block",
            "staged-target-symlink",
            display_path,
            "staged destination must be a regular path, not a symlink",
        )

    if not resolved.exists():
        return findings
    if not resolved.is_file():
        add(
            findings,
            "block",
            "staged-target-not-file",
            display_path,
            "staged destination exists but is not a regular file",
        )
        return findings

    try:
        existing_text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        add(
            findings,
            "block",
            "staged-target-unreadable",
            display_path,
            "could not read the existing staged destination to verify ownership",
        )
        return findings

    existing_fm, _ = split_frontmatter(existing_text)
    if existing_fm.get("author") != "kb-bot":
        add(
            findings,
            "block",
            "staged-target-human-owned",
            display_path,
            "staged validation may not replace an existing file without author: kb-bot",
        )

    if existing_fm.get("type") == "source" or is_source_note_path(resolved, kb_root):
        add(
            findings,
            "block",
            "staged-source-replacement",
            display_path,
            "source-note bodies are immutable; create a new source note instead of replacing this destination",
        )

    return findings


def validate_text(text: str, candidate_path: Path, kb_root: Path, *, staged: bool | None = None) -> list[Finding]:
    findings: list[Finding] = []
    kb_root = kb_root.resolve()
    try:
        resolved = candidate_path.resolve()
    except (OSError, RuntimeError):
        add(findings, "block", "candidate-path-unresolvable", str(candidate_path), "candidate path cannot be resolved safely")
        return findings
    display_path = str(candidate_path)
    if is_relative_to(resolved, kb_root):
        display_path = resolved.relative_to(kb_root).as_posix()
    else:
        add(findings, "block", "candidate-outside-kb", display_path, "candidate path is outside the KB root")
        return findings

    # A registry is optional, but once present it is part of the shared URL
    # identity contract.  Validation reports malformed registry state as a
    # blocking finding instead of allowing one caller to ignore it while the
    # publisher stops later.
    try:
        load_kb_seen().load_alias_registry(kb_root)
    except Exception as exc:
        add(
            findings,
            "block",
            "invalid-url-alias-registry",
            str(kb_root / "_url-aliases.json"),
            str(exc),
        )

    if staged is None:
        # Callers that validate an in-memory draft historically used this
        # function directly, while positional validation reads the current
        # immutable file first.  Preserve the latter by treating an exact
        # on-disk match as read-only and treating differing text as staged.
        staged = False
        if resolved.exists():
            if not resolved.is_file():
                staged = True
            else:
                try:
                    staged = resolved.read_text(encoding="utf-8") != text
                except (OSError, UnicodeDecodeError):
                    staged = True

    if staged:
        findings.extend(validate_staged_target(candidate_path, resolved, kb_root, display_path))

    fm, body = split_frontmatter(text)
    note_type = fm.get("type", "")

    if note_type == "source":
        try:
            # Keep new source candidates on the same identity contract used by
            # read-only dedup.  Historical unrelated prose may remain malformed
            # YAML, but selected identity fields must be unique top-level
            # scalars before an immutable note can be published.
            load_kb_seen()._identity_frontmatter(text)
        except Exception as exc:
            add(
                findings,
                "block",
                "ambiguous-source-identity",
                display_path,
                f"source identity fields are ambiguous: {exc}",
            )

    if candidate_path.exists() and not is_user_owned_exception(resolved, kb_root) and fm.get("author") != "kb-bot":
        add(findings, "block", "authorship", display_path, "existing candidate lacks author: kb-bot frontmatter")

    if note_type == "source":
        required = ("author", "type", "created", "topic", "title", "origin")
        missing = [field for field in required if not fm.get(field)]
        if fm.get("author") and fm.get("author") != "kb-bot":
            add(findings, "block", "authorship", display_path, "source candidate author is not kb-bot")
        if missing:
            add(findings, "block", "source-required-frontmatter", display_path, "missing source fields: " + ", ".join(missing))
        if not re.search(r"^#\s+\S+", body, flags=re.MULTILINE):
            add(findings, "block", "missing-h1", display_path, "source candidate body lacks an H1 title")
        else:
            h1 = re.search(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
            if h1 and fm.get("title") and h1.group(1).strip() != fm["title"].strip():
                add(
                    findings,
                    "block",
                    "title-h1-mismatch",
                    display_path,
                    "source frontmatter title must match the H1 exactly",
                    body[: h1.start()].count("\n") + 1,
                )
        if GENERIC_SOCIAL_TITLE_RE.match(str(fm.get("title", ""))):
            add(
                findings,
                "block",
                "generic-social-title",
                display_path,
                "social-source titles must name the subject or claim, not only the author and status ID",
            )
        if GENERIC_SOCIAL_DESCRIPTION_RE.match(str(fm.get("description", ""))):
            add(
                findings,
                "block",
                "generic-social-description",
                display_path,
                "social-source descriptions must identify the material claim or topic, not use a generic capture formula",
            )
        # A source note is an immutable capture.  Editorial synthesis belongs
        # in a mutable concept/synthesis note; source prose may state the
        # source's content and explicitly bound its evidential force, but must
        # not add a free-standing agent-analysis section that cannot later be
        # corrected without deprecating the record.
        for lineno, line in enumerate(body.splitlines(), 1):
            if SOURCE_COMMENTARY_HEADING_RE.match(line):
                add(
                    findings,
                    "block",
                    "source-editorial-heading",
                    display_path,
                    "source notes may not use editorial headings such as 'KB Context'; move analysis to a mutable wiki note",
                    lineno,
                )
            if SOURCE_AGENT_INFERENCE_RE.search(line):
                add(
                    findings,
                    "block",
                    "source-agent-inference",
                    display_path,
                    "source notes may not contain agent inference; move it to a mutable concept or synthesis note",
                    lineno,
                )
        # Every successfully captured source must preserve a provenance-bearing
        # content record and say what its evidence does *not* establish.  These
        # are intentionally source-facing headings, unlike KB Context/Analysis,
        # so they remain safe inside an immutable source record.
        if fm.get("ingest_status") != "failed":
            record = source_section(body, "Source-scoped Record")
            if record is None:
                add(
                    findings,
                    "block",
                    "missing-source-scoped-record",
                    display_path,
                    "successful source notes require a '## Source-scoped Record' section with source-attributed content",
                )
            elif meaningful_word_count(record[1]) < 8:
                add(
                    findings,
                    "warn",
                    "source-record-too-thin",
                    display_path,
                    "source-scoped record has fewer than 8 words; preserve the material claim, result, or quotation",
                    record[0],
                )

            boundary = source_section(body, "Evidence Boundary")
            if boundary is None:
                add(
                    findings,
                    "block",
                    "missing-evidence-boundary",
                    display_path,
                    "successful source notes require a '## Evidence Boundary' section",
                )
            elif meaningful_word_count(boundary[1]) < 8:
                add(
                    findings,
                    "warn",
                    "evidence-boundary-too-thin",
                    display_path,
                    "evidence boundary has fewer than 8 words; state the source's evidential limits",
                    boundary[0],
                )

            if fm.get("topic") == "health" and boundary is not None and not HEALTH_EVIDENCE_BOUNDARY_RE.search(boundary[1]):
                add(
                    findings,
                    "block",
                    "health-evidence-boundary-insufficient",
                    display_path,
                    "health source evidence boundary must explicitly state that the source does not establish clinical/medical guidance, treatment, diagnosis, safety, causality, or a recommendation",
                    boundary[0],
                )

            # Dense material must expose its coverage in the source record so a
            # second pass can verify that key claims, methods, qualifiers, and
            # limitations were not silently dropped during compression.
            if DENSE_SOURCE_TYPE_RE.search(body) and source_section(body, "Content Map") is None:
                add(
                    findings,
                    "block",
                    "missing-content-map",
                    display_path,
                    "article/paper/thread/transcript candidates require a '## Content Map' with source-attributed coverage units",
                )
            elif DENSE_SOURCE_TYPE_RE.search(body):
                content_map = source_section(body, "Content Map")
                assert content_map is not None
                if meaningful_word_count(content_map[1]) < 12:
                    add(
                        findings,
                        "warn",
                        "content-map-too-thin",
                        display_path,
                        "content map has fewer than 12 words; enumerate substantive source units rather than its thesis alone",
                        content_map[0],
                    )

        origin = fm.get("origin", "")
        if urlparse(str(origin)).netloc.casefold() in {"fxtwitter.com", "api.fxtwitter.com"}:
            add(
                findings,
                "block",
                "noncanonical-social-origin",
                display_path,
                "use the canonical x.com URL as origin and retain fxtwitter only in mirror",
            )
        validate_source_media(fm.get("media"), findings, display_path)
    elif note_type and fm.get("author") and fm.get("author") != "kb-bot":
        add(findings, "block", "authorship", display_path, "candidate author is not kb-bot")

    ingest_status = fm.get("ingest_status")
    if ingest_status and ingest_status not in VALID_INGEST_STATUS:
        add(
            findings,
            "block",
            "invalid-ingest-status",
            display_path,
            f"ingest_status must be one of {sorted(VALID_INGEST_STATUS)}",
        )

    for lineno, target in wikilinks_outside_code(text):
        if target.startswith(("http://", "https://")):
            add(findings, "block", "external-url-wikilink", display_path, f"use Markdown links for URLs, not [[{target}]]", lineno)
        if target.startswith("../") or "/../" in target:
            add(findings, "block", "relative-wikilink", display_path, f"use vault-rooted wikilinks, not [[{target}]]", lineno)

    for lineno, value in evidence_quality_values(text):
        if value not in EVIDENCE_QUALITY_VALUES:
            add(
                findings,
                "block",
                "invalid-evidence-quality",
                display_path,
                f"evidence_quality must be one of {sorted(EVIDENCE_QUALITY_VALUES)}",
                lineno,
            )

    for field in RECOMMENDED_FIELDS:
        if field not in fm:
            add(findings, "warn", "recommended-frontmatter", display_path, f"missing recommended field: {field}")
    if "description" not in fm:
        add(findings, "warn", "recommended-frontmatter", display_path, "missing recommended field: description")
    if "modified" not in fm:
        add(findings, "warn", "recommended-frontmatter", display_path, "missing recommended field: modified")

    if note_type in {"concept", "synthesis", "output", "entity"} and not fm.get("sources") and "[[knowledgebase/topics/" not in body:
        add(findings, "warn", "wiki-note-no-citations", display_path, "wiki/output candidate has no obvious source-note citation")

    local_path = fm.get("local_path")
    if local_path:
        media_path = kb_root.parent / local_path
        if not media_path.exists():
            add(findings, "warn", "missing-local-media", display_path, f"local_path does not exist: {local_path}")

    if note_type == "media-description" and nonempty_value(fm.get("media_id")):
        media_id = str(fm["media_id"])
        for existing_path in kb_root.glob("topics/*/notes/media/*.md"):
            if existing_path.resolve() == resolved:
                continue
            try:
                existing_fm, _ = split_frontmatter(existing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(existing_fm.get("media_id", "")) == media_id:
                add(
                    findings,
                    "block",
                    "duplicate-media-description-id",
                    display_path,
                    f"media_id {media_id!r} already belongs to {existing_path.relative_to(kb_root).as_posix()}; reuse that description note instead of creating a duplicate",
                )
                break

    if note_type == "source":
        existing = find_existing_origin(kb_root, str(fm.get("origin", "")), resolved)
        if existing:
            add(
                findings,
                "warn",
                "origin-collision",
                display_path,
                f"origin matches existing source note: {existing.relative_to(kb_root).as_posix()}",
            )
        try:
            alias_existing = find_alias_collision(kb_root, str(fm.get("origin", "")), resolved)
        except Exception as exc:
            add(
                findings,
                "block",
                "invalid-url-alias-registry",
                str(kb_root / "_url-aliases.json"),
                str(exc),
            )
        else:
            if alias_existing is not None:
                add(
                    findings,
                    "block",
                    "url-alias-collision",
                    display_path,
                    "origin is a governed legacy alias for existing source note: "
                    + alias_existing.relative_to(kb_root).as_posix(),
                )

    return findings


def validate(candidate_path: Path, kb_root: Path) -> list[Finding]:
    try:
        text = candidate_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [Finding("block", "candidate-missing", str(candidate_path), "candidate file does not exist")]
    return validate_text(text, candidate_path, kb_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a KB source/media candidate before commit.")
    parser.add_argument("candidate", nargs="?", help="Path to an on-disk candidate Markdown note.")
    parser.add_argument(
        "--staged-text",
        help="Read draft Markdown from this path while validating it as --candidate-path; enables validation before immutable write.",
    )
    parser.add_argument(
        "--candidate-path",
        help="Vault-relative logical destination for --staged-text (for example topics/foo/sources/date-note.md).",
    )
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true", help="Print JSON findings.")
    return parser


def print_text(findings: list[Finding]) -> None:
    for finding in findings:
        line = f":{finding.line}" if finding.line is not None else ""
        print(f"{finding.severity.upper()} {finding.code} {finding.path}{line} — {finding.message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_root = Path(args.kb_root)
    if args.staged_text:
        if args.candidate:
            raise SystemExit("use either a positional candidate or --staged-text, not both")
        if not args.candidate_path:
            raise SystemExit("--staged-text requires --candidate-path")
        logical = Path(args.candidate_path)
        candidate_path = logical if logical.is_absolute() else kb_root / logical
        try:
            text = Path(args.staged_text).read_text(encoding="utf-8")
        except FileNotFoundError:
            findings = [Finding("block", "staged-text-missing", args.staged_text, "staged text file does not exist")]
        else:
            findings = validate_text(text, candidate_path, kb_root, staged=True)
    else:
        if not args.candidate:
            raise SystemExit("provide a positional candidate or --staged-text")
        findings = validate(Path(args.candidate), kb_root)
    if args.json:
        payload: dict[str, Any] = {"finding_count": len(findings), "findings": [asdict(finding) for finding in findings]}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_text(findings)
    if any(finding.severity == "block" for finding in findings):
        return 2
    if findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
