#!/usr/bin/env python3
"""Pure, fence-aware parsing for KB queue Markdown.

The queue files are Markdown, but their approval meaning is deliberately much
narrower than Markdown's rendering model.  This module provides the one
interpretation used by read-only preflight and the guarded publication and
checkoff paths:

* YAML frontmatter is never a queue body;
* fenced code (backticks or tildes) is never a queue body;
* blockquotes and indented code are never queue headings or rows; and
* only canonical level-two section headings become active queue sections.

The parser has no filesystem or network side effects.  A row retains its exact
line text and one-based source line so callers can select a row without
reconstructing or normalising its primary locator.  Consumers are
responsible for deciding which parser diagnostics are fatal for their operation
(for example, an approval publisher must reject duplicate approval headings,
while a prose inbox checkoff need not care about them).
"""

from dataclasses import dataclass
import importlib.util
import re
import sys
from pathlib import Path


CANONICAL_SECTIONS = (
    "Pending",
    "Approved",
    "Auto-approved",
    "Processed",
    "Failed — Retry",
)
APPROVAL_SECTIONS = ("Approved", "Auto-approved")
_CANONICAL_BY_FOLDED = {name.casefold(): name for name in CANONICAL_SECTIONS}
_APPROVAL_BY_FOLDED = {name.casefold(): name for name in APPROVAL_SECTIONS}

CHECKBOX_RE = re.compile(r"^(?P<prefix>\s*-\s+\[(?P<mark>[ xX!~?])\])")
_HEADING_RE = re.compile(r"^(?P<indent> {0,3})##[ \t]+(?P<title>.+?)[ \t]*$")
_FENCE_RE = re.compile(r"^(?P<indent> {0,3})(?P<run>`{3,}|~{3,})(?P<info>.*)$")
_HTML_TAG_RE = re.compile(r"^</?(?P<tag>[A-Za-z][A-Za-z0-9:-]*)(?:\s[^>]*)?>")
_VOID_HTML_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))


class QueueParseError(ValueError):
    """Raised when an approval-sensitive caller cannot use queue structure."""


def _without_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _indent_width(line: str) -> int:
    """Return a conservative Markdown indentation width for a source line."""
    width = 0
    for character in line:
        if character == " ":
            width += 1
        elif character == "\t":
            # A tab at the beginning of a line is indented code for our safety
            # boundary.  We do not need Markdown's tab-stop arithmetic here.
            width = 4
            break
        else:
            break
    return width


def _is_blockquote_or_indented_code(line: str) -> bool:
    if _indent_width(line) >= 4:
        return True
    # Up to three spaces may precede a blockquote marker.  Do not strip or
    # rewrite the source; this test only decides whether the line is semantic
    # queue content.
    return line.lstrip(" ").startswith(">")


def _has_unclosed_comment(text: str) -> bool:
    """Whether one or more HTML comment openers lacks a later close."""
    cursor = 0
    while True:
        opening = text.find("<!--", cursor)
        if opening < 0:
            return False
        closing = text.find("-->", opening + 4)
        if closing < 0:
            return True
        cursor = closing + 3


def primary_locator(row: str) -> str | None:
    """Return the exact primary URL/PDF token from one queue row.

    Delegate to the existing source-completion selector so the queue parser
    cannot drift from the established exact-token contract.
    """
    if not isinstance(row, str):
        return None
    key = "kb_queue_parser_source_completion"
    module = sys.modules.get(key)
    if module is None:
        script = Path(__file__).with_name("kb-source-completion.py")
        spec = importlib.util.spec_from_file_location(key, script)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load kb-source-completion.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return module.primary_locator(row)


@dataclass(frozen=True)
class QueueHeading:
    """One semantic level-two heading."""

    line: int
    raw: str
    name: str
    canonical_name: str | None
    is_approval: bool

    @property
    def canonical(self) -> bool:
        return self.canonical_name is not None and self.name == self.canonical_name


@dataclass(frozen=True)
class QueueRow:
    """One real checkbox row, with exact source positions."""

    line: int
    raw: str
    raw_line: str
    section: str | None
    section_line: int | None
    mark: str
    locator: str | None


@dataclass(frozen=True)
class QueueDocument:
    """Pure parse result for one queue text value."""

    source: str
    rows: tuple[QueueRow, ...]
    headings: tuple[QueueHeading, ...]
    frontmatter_end_line: int
    frontmatter_error: str | None
    unclosed_fence: tuple[str, int] | None
    unclosed_comment: int | None
    unsupported_html_line: int | None
    malformed_sections: tuple[str, ...]
    malformed_approval_headings: tuple[str, ...]
    duplicate_sections: tuple[str, ...]
    duplicate_approval_sections: tuple[str, ...]

    @property
    def approval_safe(self) -> bool:
        """Whether canonical approval headings are unique and well formed."""
        return not (
            self.frontmatter_error
            or self.malformed_approval_headings
            or self.duplicate_approval_sections
            or self.unclosed_fence
            or self.unclosed_comment
            or self.unsupported_html_line
        )

    @property
    def errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.frontmatter_error:
            errors.append(self.frontmatter_error)
        errors.extend(
            f"noncanonical section heading {heading!r}"
            for heading in self.malformed_sections
        )
        errors.extend(
            f"duplicate section heading {heading!r}"
            for heading in self.duplicate_sections
        )
        if self.unclosed_fence:
            character, line = self.unclosed_fence
            errors.append(f"unclosed {character}-fence opened at line {line}")
        if self.unclosed_comment:
            errors.append(f"unclosed HTML comment opened at line {self.unclosed_comment}")
        if self.unsupported_html_line:
            errors.append(f"unsupported raw HTML block at line {self.unsupported_html_line}")
        return tuple(errors)

    def require_approval_safe(self) -> None:
        """Raise a stable error for publication/completion approval checks."""
        if self.frontmatter_error:
            raise QueueParseError(self.frontmatter_error)
        if self.malformed_approval_headings:
            name = self.malformed_approval_headings[0]
            canonical = _APPROVAL_BY_FOLDED.get(name.casefold(), name)
            raise QueueParseError(
                f"noncanonical section heading {name!r}; use {canonical!r}"
            )
        if self.duplicate_approval_sections:
            names = ", ".join(repr(name) for name in self.duplicate_approval_sections)
            raise QueueParseError(f"duplicate approval section heading(s): {names}")
        if self.unclosed_fence:
            character, line = self.unclosed_fence
            raise QueueParseError(f"unclosed {character}-fence opened at line {line}")
        if self.unclosed_comment:
            raise QueueParseError(
                f"unclosed HTML comment opened at line {self.unclosed_comment}"
            )
        if self.unsupported_html_line:
            raise QueueParseError(
                f"unsupported raw HTML block at line {self.unsupported_html_line}"
            )


def _frontmatter_boundary(lines: list[str]) -> tuple[int, str | None]:
    """Return the first body line index and an unclosed-frontmatter error."""
    if not lines or _without_line_ending(lines[0]) != "---":
        return 0, None
    for index in range(1, len(lines)):
        if _without_line_ending(lines[index]) in {"---", "..."}:
            return index + 1, None
    return len(lines), "unterminated YAML frontmatter"


def parse_queue(text: str) -> QueueDocument:
    """Parse queue semantics without touching the filesystem."""
    if not isinstance(text, str):
        raise TypeError("queue text must be a string")

    lines = text.splitlines(keepends=True)
    body_start, frontmatter_error = _frontmatter_boundary(lines)
    headings: list[QueueHeading] = []
    rows: list[QueueRow] = []
    malformed_sections: list[str] = []
    malformed_approval: list[str] = []
    section_counts: dict[str, int] = {}
    active_section: str | None = None
    active_heading_line: int | None = None
    fence_character: str | None = None
    fence_length = 0
    fence_line: int | None = None
    comment_line: int | None = None
    html_block_tag: str | None = None
    unsupported_html_line: int | None = None

    for index, raw_line in enumerate(lines[body_start:], body_start + 1):
        line = _without_line_ending(raw_line)
        if fence_character is not None:
            match = _FENCE_RE.match(line)
            if (
                match is not None
                and match.group("run")[0] == fence_character
                and len(match.group("run")) >= fence_length
                and not match.group("info").strip()
            ):
                fence_character = None
                fence_length = 0
                fence_line = None
            continue

        if comment_line is not None:
            if "-->" in line:
                comment_line = index if _has_unclosed_comment(line.split("-->", 1)[1]) else None
            continue

        if html_block_tag is not None:
            if re.search(rf"</{re.escape(html_block_tag)}(?:\s|>)", line, re.IGNORECASE):
                html_block_tag = None
            continue

        match = _FENCE_RE.match(line)
        if match is not None:
            fence_character = match.group("run")[0]
            fence_length = len(match.group("run"))
            fence_line = index
            continue

        if _is_blockquote_or_indented_code(line):
            continue

        # An inline HTML comment can hide a heading on a following line too;
        # skip the complete opener line and remain conservative if it has no
        # matching close.  Queue rows do not use HTML comments as data, so
        # losing an annotated row is safer than treating an example as live.
        comment_start = line.find("<!--")
        if comment_start >= 0:
            # A commented heading is not canonical and must end the previous
            # section. Ordinary comments, like fenced examples, are neutral:
            # they neither grant approval nor erase the surrounding section.
            if _HEADING_RE.match(line):
                active_section = None
                active_heading_line = None
            remainder = line[comment_start:]
            if _has_unclosed_comment(remainder):
                comment_line = index
            continue

        stripped = line.lstrip(" ")
        html_tag = _HTML_TAG_RE.match(stripped)
        if html_tag is not None:
            if unsupported_html_line is None:
                unsupported_html_line = index
            tag = html_tag.group("tag").casefold()
            tail = line[html_tag.end() :]
            if (
                tag not in _VOID_HTML_TAGS
                and not html_tag.group(0).rstrip().endswith("/>")
                and not re.search(rf"</{re.escape(tag)}(?:\s|>)", tail, re.IGNORECASE)
            ):
                html_block_tag = tag
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match is not None:
            name = heading_match.group("title").strip()
            folded = name.casefold()
            canonical_name = _CANONICAL_BY_FOLDED.get(folded)
            is_approval = folded in _APPROVAL_BY_FOLDED
            headings.append(
                QueueHeading(
                    line=index,
                    raw=line,
                    name=name,
                    canonical_name=canonical_name,
                    is_approval=is_approval,
                )
            )
            if canonical_name is None or name != canonical_name:
                active_section = None
                active_heading_line = None
                if folded in _CANONICAL_BY_FOLDED:
                    malformed_sections.append(name)
                    if is_approval:
                        malformed_approval.append(name)
            else:
                # Only exact canonical spelling is an active queue section.
                active_section = canonical_name
                active_heading_line = index
                section_counts[canonical_name] = section_counts.get(canonical_name, 0) + 1
            continue

        checkbox = CHECKBOX_RE.match(line)
        if checkbox is None:
            continue
        rows.append(
            QueueRow(
                line=index,
                raw=line,
                raw_line=raw_line,
                section=active_section,
                section_line=active_heading_line,
                mark=checkbox.group("mark"),
                locator=primary_locator(line),
            )
        )

    duplicate_sections = tuple(
        name for name in CANONICAL_SECTIONS if section_counts.get(name, 0) > 1
    )
    duplicate_approval = tuple(
        name for name in APPROVAL_SECTIONS if section_counts.get(name, 0) > 1
    )
    unclosed = (
        (fence_character, fence_line)
        if fence_character is not None and fence_line is not None
        else None
    )
    return QueueDocument(
        source=text,
        rows=tuple(rows),
        headings=tuple(headings),
        frontmatter_end_line=body_start,
        frontmatter_error=frontmatter_error,
        unclosed_fence=unclosed,
        unclosed_comment=comment_line,
        unsupported_html_line=unsupported_html_line,
        malformed_sections=tuple(malformed_sections),
        malformed_approval_headings=tuple(malformed_approval),
        duplicate_sections=duplicate_sections,
        duplicate_approval_sections=duplicate_approval,
    )


# Small compatibility aliases make the pure parser convenient to import from
# callers/tests without creating a second parser implementation.
parse = parse_queue
parse_text = parse_queue


def section_insert_index(text: str, section: str) -> int | None:
    """Return a safe 0-based line insertion point for one queue section.

    The section must have one exact canonical heading and the document must
    have no malformed/duplicate canonical headings or unterminated Markdown
    fence/HTML comment.  The returned point is immediately before the next
    semantic level-two heading (or at EOF), with trailing blank lines before
    that boundary trimmed so callers can insert without landing inside an
    example block.
    """
    if section not in CANONICAL_SECTIONS:
        return None
    parsed = parse_queue(text)
    if (
        parsed.frontmatter_error
        or parsed.malformed_sections
        or parsed.duplicate_sections
        or parsed.unclosed_fence
        or parsed.unclosed_comment
        or parsed.unsupported_html_line
    ):
        return None
    candidates = [heading for heading in parsed.headings if heading.name == section]
    if len(candidates) != 1:
        return None
    heading = candidates[0]
    next_headings = [item for item in parsed.headings if item.line > heading.line]
    boundary = (next_headings[0].line - 1) if next_headings else len(text.splitlines(keepends=True))
    lines = text.splitlines(keepends=True)
    insertion = boundary
    while insertion > heading.line and _without_line_ending(lines[insertion - 1]).strip() == "":
        insertion -= 1
    return insertion


if __name__ == "__main__":
    import json
    import sys

    parsed = parse_queue(sys.stdin.read())
    print(
        json.dumps(
            {
                "rows": [
                    {
                        "line": row.line,
                        "section": row.section,
                        "raw": row.raw,
                        "locator": row.locator,
                    }
                    for row in parsed.rows
                ],
                "errors": list(parsed.errors),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
