from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-queue-parse.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_queue_parse_tests", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ordinary_comments_do_not_erase_existing_approval():
    module = load_module()
    parsed = module.parse_queue(
        "## Approved\n<!-- explanatory note -->\n"
        "example <!--\n## Pending\n- [ ] https://example.test/hidden\n-->\n"
        "- [ ] https://example.test/real\n"
    )
    parsed.require_approval_safe()
    assert [(row.locator, row.section) for row in parsed.rows] == [
        ("https://example.test/real", "Approved")
    ]


def test_commented_heading_cannot_inherit_previous_approval():
    module = load_module()
    parsed = module.parse_queue(
        "## Approved\n## Pending <!-- explanation -->\n"
        "- [ ] https://example.test/not-approved\n"
    )
    assert parsed.rows[0].section is None


def test_frontmatter_and_fenced_examples_never_create_approval():
    module = load_module()
    text = (
        "---\n"
        "author: kb-bot\n"
        "description: |\n"
        "  ## Approved\n"
        "---\n"
        "## Pending\n"
        "```markdown\n"
        "## Approved\n"
        "- [ ] https://example.test/fake\n"
        "```\n"
        "## Approved\n"
        "- [ ] https://example.test/real — café\n"
    )

    parsed = module.parse_queue(text)

    assert [(row.line, row.section, row.locator) for row in parsed.rows] == [
        (12, "Approved", "https://example.test/real")
    ]
    assert parsed.rows[0].raw == "- [ ] https://example.test/real — café"
    assert parsed.rows[0].raw_line.endswith("\n")
    assert parsed.rows[0].section_line == 11
    assert parsed.approval_safe


def test_fences_require_matching_character_and_at_least_opening_length():
    module = load_module()
    text = (
        "## Approved\n"
        "~~~markdown\n"
        "- [ ] https://example.test/inside-one\n"
        "````\n"
        "- [ ] https://example.test/inside-two\n"
        "~~~~\n"
        "- [ ] https://example.test/after\n"
    )

    parsed = module.parse_queue(text)

    assert [row.locator for row in parsed.rows] == ["https://example.test/after"]


def test_blockquotes_and_indented_code_do_not_change_active_section():
    module = load_module()
    text = (
        "## Pending\n"
        "> ## Approved\n"
        "> - [ ] https://example.test/quoted\n"
        "    ## Approved\n"
        "    - [ ] https://example.test/indented\n"
        "- [ ] https://example.test/pending\n"
        "## Approved\n"
        "- [ ] https://example.test/approved\n"
    )

    parsed = module.parse_queue(text)

    assert [(row.locator, row.section) for row in parsed.rows] == [
        ("https://example.test/pending", "Pending"),
        ("https://example.test/approved", "Approved"),
    ]


def test_malformed_and_duplicate_approval_headings_fail_closed():
    module = load_module()
    malformed = module.parse_queue(
        "## approved\n- [ ] https://example.test/wrong-case\n"
    )
    duplicate = module.parse_queue(
        "## Approved\n- [ ] https://example.test/one\n"
        "## Approved\n- [ ] https://example.test/two\n"
    )

    assert malformed.rows[0].section is None
    assert malformed.malformed_approval_headings == ("approved",)
    assert not malformed.approval_safe
    assert duplicate.duplicate_approval_sections == ("Approved",)
    assert not duplicate.approval_safe


def test_section_insert_index_uses_semantic_heading_and_rejects_ambiguous_markup():
    module = load_module()
    text = (
        "---\n"
        "author: kb-bot\n"
        "---\n"
        "## Pending\n"
        "- [ ] https://example.test/pending\n"
        "\n"
        "## Approved\n"
        "\n"
        "### Group\n"
        "- [ ] https://example.test/approved\n"
        "\n"
        "## Auto-approved\n"
    )

    assert module.section_insert_index(text, "Approved") == 10
    assert module.section_insert_index(text, "Nope") is None
    assert module.section_insert_index(text, "Pending") == 5

    fenced = text.replace("## Auto-approved\n", "```markdown\n## Auto-approved\n")
    assert module.section_insert_index(fenced, "Approved") is None

    duplicate = text.replace("## Auto-approved\n", "## Approved\n")
    assert module.section_insert_index(duplicate, "Approved") is None

    commented = text.replace(
        "## Approved\n",
        "<!-- example queue\n## Approved\n- [ ] https://example.test/commented\n-->\n## Approved\n",
        1,
    )
    parsed = module.parse_queue(commented)
    assert [row.locator for row in parsed.rows] == [
        "https://example.test/pending",
        "https://example.test/approved",
    ]
    assert module.section_insert_index(commented, "Approved") == 14

    inline = text.replace(
        "## Approved\n",
        "example <!-- hidden queue\n## Approved\n- [ ] https://example.test/inline-fake\n-->\n## Approved\n",
        1,
    )
    inline_parsed = module.parse_queue(inline)
    assert [row.locator for row in inline_parsed.rows] == [
        "https://example.test/pending",
        "https://example.test/approved",
    ]
    assert inline_parsed.approval_safe

    comment_heading = (
        "## Approved\n"
        "## Pending <!-- note -->\n"
        "- [ ] https://example.test/not-approved\n"
    )
    comment_heading_parsed = module.parse_queue(comment_heading)
    assert comment_heading_parsed.rows[0].section is None

    unclosed_comment = text.replace("## Approved\n", "<!--\n## Approved\n", 1)
    assert module.section_insert_index(unclosed_comment, "Approved") is None

    raw_html = (
        "<div class=\"example\">\n"
        "## Approved\n"
        "- [ ] https://example.test/html-fake\n"
        "</div>\n"
        "## Approved\n"
        "- [ ] https://example.test/real\n"
    )
    raw_html_parsed = module.parse_queue(raw_html)
    assert [row.locator for row in raw_html_parsed.rows] == [
        "https://example.test/real"
    ]
    assert raw_html_parsed.unsupported_html_line == 1
    assert not raw_html_parsed.approval_safe
