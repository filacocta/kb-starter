from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-validate-source-candidate.py"


def load_validator_module():
    spec = importlib.util.spec_from_file_location("kb_validate_source_candidate", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_candidate(kb_root: Path, rel_path: str, text: str) -> Path:
    path = kb_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def severities(findings) -> set[str]:
    return {finding.severity for finding in findings}


def valid_source_text(title: str = "Staged Source", origin: str = "https://example.com/staged") -> str:
    return f"""---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: {title}
description: A staged source candidate.
status: draft
tags: []
sources: []
related: []
origin: "{origin}"
ingest_status: succeeded
media: []
---

# {title}

## Source-scoped Record

The source reports a concrete result and names the underlying material.

## Evidence Boundary

This captures the source's report and does not independently validate it.
"""


def test_good_source_candidate_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/good.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Good Source
description: A source candidate.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/source"
ingest_status: succeeded
media: []
---

# Good Source

## Source-scoped Record

The source reports one concrete result from its primary material.

## Evidence Boundary

The record is limited to what this source reports and does not independently establish causation.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert findings == []
    assert validator.main([str(candidate), "--kb-root", str(kb_root)]) == 0


def test_source_candidate_rejects_ambiguous_identity_fields(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/ambiguous.md",
        valid_source_text().replace(
            "origin: \"https://example.com/staged\"",
            "origin: \"https://example.com/first\"\norigin: \"https://example.com/staged\"",
        ),
    )

    findings = validator.validate(candidate, kb_root)

    assert "ambiguous-source-identity" in codes(findings)
    assert "block" in severities(findings)


def test_source_candidate_rejects_immutable_editorial_context(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/editorial.md",
        """---
author: kb-bot
created: 2026-08-07
modified: 2026-08-07
type: source
topic: example
title: Editorial Source
description: A source candidate with an invalid editorial section.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/source"
ingest_status: succeeded
---

# Editorial Source

## Mirrored Text

The source makes a claim.

## KB Context

An agent's unsourced conclusion.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "source-editorial-heading" in codes(findings)
    assert "block" in severities(findings)


def test_source_candidate_rejects_immutable_kb_context_section(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/editorialized.md",
        """---
author: kb-bot
created: 2026-08-07
modified: 2026-08-07
type: source
topic: example
title: Editorialized Source
description: A source candidate with an invalid editorial section.
tags: []
sources: []
related: []
origin: "https://example.com/source"
---

# Editorialized Source

## Mirrored Text

The author made a claim.

## KB Context

An uncited editorial conclusion.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "source-editorial-heading" in codes(findings)
    assert "block" in severities(findings)


def test_successful_source_requires_provenance_and_evidence_sections(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/incomplete.md",
        """---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: Incomplete Source
description: A candidate missing the quality gate sections.
tags: []
sources: []
related: []
origin: "https://example.com/incomplete"
ingest_status: succeeded
---

# Incomplete Source

Captured source summary.
""",
    )

    assert {"missing-source-scoped-record", "missing-evidence-boundary"} <= codes(validator.validate(candidate, kb_root))


def test_dense_source_requires_content_map(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/article.md",
        """---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: Article Source
description: An article candidate missing its coverage map.
tags: []
sources: []
related: []
origin: "https://example.com/article"
ingest_status: succeeded
---

# Article Source

**Source type:** Long-form article

## Source-scoped Record

The article reports a concrete method, result, qualifier, and limitation from the author.

## Evidence Boundary

This record only captures the source's claims and is not independent validation.
""",
    )

    assert "missing-content-map" in codes(validator.validate(candidate, kb_root))


def test_social_mirror_transcript_is_not_misclassified_as_dense(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/social.md",
        """---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: Social Source
description: A short social candidate.
tags: []
sources: []
related: []
origin: "https://x.com/example/status/123"
ingest_status: succeeded
---

# Social Source

**Source type:** Social-media post (FxTwitter API mirror transcript)

## Source-scoped Record

The author makes one source-attributed claim without a linked study or underlying data.

## Evidence Boundary

This social claim is not independent evidence and does not establish a general result.
""",
    )

    assert "missing-content-map" not in codes(validator.validate(candidate, kb_root))


def test_yaml_frontmatter_preserves_quoted_url_and_trailing_apostrophe(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/quoted.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: example
title: "Maker — 'A Claim'"
description: "A title whose significant final character is an apostrophe."
tags: []
sources: []
related: []
origin: "https://x.com/maker/status/123"
mirror: "https://api.fxtwitter.com/2/thread/123"
ingest_status: succeeded
media: []
---

# Maker — 'A Claim'

## Source-scoped Record

The source makes one concrete claim and is preserved as a source-attributed record.

## Evidence Boundary

The record only captures the source's claim and does not independently establish causality.
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_source_rejects_generic_social_metadata_and_agent_inference(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/generic-social.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: example
title: "@example — Health-related claim record (123)"
description: "A captured social-media source record by @example."
tags: []
sources: []
related: []
origin: "https://x.com/example/status/123"
ingest_status: succeeded
media: []
---

# @example — Health-related claim record (123)

## Source-scoped Record

The author makes an uncited source claim about the named health topic.

> Agent inference — the claim is likely useful.

## Evidence Boundary

This social claim is not independent evidence and does not establish a general result.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert {"generic-social-title", "generic-social-description", "source-agent-inference"} <= codes(findings)


def test_source_media_requires_complete_nested_schema(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/bad-media.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: example
title: Bad Media
description: A source candidate with a malformed discovered-media entry.
tags: []
sources: []
related: []
origin: "https://example.com/media"
ingest_status: succeeded
media:
  - status: discovered
    origin_url: "https://example.com/chart.png"
    license: quoted-with-attribution
---

# Bad Media

## Source-scoped Record

The source includes a chart that is discovered but not captured.

## Evidence Boundary

The chart is source material and does not independently establish its claim.
""",
    )

    assert "source-media-required-field" in codes(validator.validate(candidate, kb_root))


def test_captured_source_media_requires_path_date_and_description_note(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/incomplete-capture.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: example
title: Incomplete Capture
description: A source with a wrongly marked captured image.
tags: []
sources: []
related: []
origin: "https://example.com/media"
ingest_status: succeeded
media:
  - id: "hash-123"
    kind: image
    status: captured
    origin_url: "https://example.com/chart.png"
    license: quoted-with-attribution
---

# Incomplete Capture

## Source-scoped Record

The source claims the image was captured alongside its reported result.

## Evidence Boundary

The image provenance does not independently establish the claimed result.
""",
    )

    assert "captured-source-media-incomplete" in codes(validator.validate(candidate, kb_root))


def test_captured_source_media_requires_vault_rooted_description_note(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/bad-description-link.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: example
title: Bad Description Link
description: A source with a captured image pointing to a relative note.
tags: []
sources: []
related: []
origin: "https://example.com/media"
ingest_status: succeeded
media:
  - id: "hash-123"
    kind: image
    status: captured
    origin_url: "https://example.com/chart.png"
    license: quoted-with-attribution
    path: "knowledgebase/_media/images/2026/08/chart-hash.png"
    captured: 2026-08-11
    description_note: "[[notes/media/chart-hash]]"
---

# Bad Description Link

## Source-scoped Record

The source claims the image was captured alongside its reported result.

## Evidence Boundary

The image provenance does not independently establish the claimed result.
""",
    )

    assert "invalid-source-media-description-note" in codes(validator.validate(candidate, kb_root))


def test_source_media_accepts_complete_entries_and_health_boundary(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/health/sources/good-media.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: health
title: Nutrition Claim and Chart
description: An uncited social claim about nutrition and a discovered chart.
tags: [health, social-media]
sources: []
related: []
origin: "https://x.com/example/status/123"
mirror: "https://api.fxtwitter.com/2/thread/123"
ingest_status: succeeded
media:
  - id: "discovered-chart-123"
    kind: image
    status: discovered
    origin_url: "https://example.com/chart.png"
    license: quoted-with-attribution
  - id: "skipped-video-456"
    kind: video
    status: not-captured
    origin_url: "https://example.com/video.mp4"
    license: do-not-redistribute
    not_captured_reason: "The video contains procedural health guidance."
---

# Nutrition Claim and Chart

## Source-scoped Record

The author makes an uncited nutrition claim and attaches a chart as source context.

## Evidence Boundary

This social source is not independently verified clinical evidence and is not medical guidance, treatment, diagnosis, dosing, or a recommendation.
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_health_source_requires_specific_safety_boundary(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/health/sources/thin-boundary.md",
        """---
author: kb-bot
created: 2026-08-11
modified: 2026-08-11
type: source
topic: health
title: Thin Health Boundary
description: A health candidate with a generic limitation.
tags: [health]
sources: []
related: []
origin: "https://example.com/health"
ingest_status: succeeded
media: []
---

# Thin Health Boundary

## Source-scoped Record

The source makes a health claim about a supplement without a linked primary study.

## Evidence Boundary

The source is limited and requires more evidence before any conclusion.
""",
    )

    assert "health-evidence-boundary-insufficient" in codes(validator.validate(candidate, kb_root))


def test_source_rejects_mismatched_h1_and_mirror_as_origin(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/social.md",
        """---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: Canonical Social Source
description: A social candidate with invalid provenance metadata.
tags: []
sources: []
related: []
origin: "https://fxtwitter.com/example/status/123"
ingest_status: succeeded
---

# Different Heading

## Source-scoped Record

The social post reports a specific claim in its original wording.

## Evidence Boundary

The post remains an unverified source claim rather than independently established evidence.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert {"title-h1-mismatch", "noncanonical-social-origin"} <= codes(findings)


def test_validator_accepts_staged_text_at_a_logical_kb_destination(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    kb_root.mkdir()
    staged = tmp_path / "source-draft.md"
    staged.write_text(
        """---
author: kb-bot
created: 2026-08-08
modified: 2026-08-08
type: source
topic: example
title: Staged Source
description: A staged candidate.
tags: []
sources: []
related: []
origin: "https://example.com/staged"
ingest_status: succeeded
media: []
---

# Staged Source

## Source-scoped Record

The source reports a concrete result and names the underlying material.

## Evidence Boundary

This captures the source's report and does not independently validate it.
""",
        encoding="utf-8",
    )

    assert validator.main(
        [
            "--staged-text",
            str(staged),
            "--candidate-path",
            "topics/example/sources/staged.md",
            "--kb-root",
            str(kb_root),
        ]
    ) == 0


def test_staged_validation_reads_existing_human_owned_target(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    target = write_candidate(kb_root, "topics/example/notes/concepts/human.md", "# Human note\n")
    staged = tmp_path / "source-draft.md"
    staged.write_text(valid_source_text(), encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    findings = validator.validate_text(staged.read_text(encoding="utf-8"), target, kb_root)

    assert "staged-target-human-owned" in codes(findings)
    assert "block" in severities(findings)
    assert target.read_text(encoding="utf-8") == original


def test_staged_validation_blocks_existing_source_body_replacement(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    target = write_candidate(
        kb_root,
        "topics/example/sources/existing.md",
        valid_source_text("Existing Source", "https://example.com/existing"),
    )
    staged = tmp_path / "source-draft.md"
    staged.write_text(valid_source_text("Replacement Source"), encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    # Positional validation inspects the current immutable note and remains
    # read-only; staged validation must reject replacing its body.
    assert validator.validate(target, kb_root) == []
    findings = validator.validate_text(staged.read_text(encoding="utf-8"), target, kb_root, staged=True)

    assert "staged-source-replacement" in codes(findings)
    assert "block" in severities(findings)
    assert target.read_text(encoding="utf-8") == original


def test_staged_validation_rejects_symlink_escape_target(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    outside = tmp_path / "outside.md"
    outside.write_text(valid_source_text(), encoding="utf-8")
    target = kb_root / "topics/example/sources/escape.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    staged = tmp_path / "source-draft.md"
    staged.write_text(valid_source_text(), encoding="utf-8")

    findings = validator.validate_text(staged.read_text(encoding="utf-8"), target, kb_root, staged=True)

    assert codes(findings) == {"candidate-outside-kb"}
    assert target.is_symlink()


def test_local_file_source_origin_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    local_pdf = tmp_path / "report.pdf"
    local_pdf.write_bytes(b"synthetic PDF placeholder")
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/local-file.md",
        f"""---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Local File
description: A local file candidate.
status: draft
tags: []
sources: []
related: []
origin: "{local_pdf}"
media: []
---

# Local File

## Source-scoped Record

The local report records its observed result and the material it examined.

## Evidence Boundary

This record preserves the report rather than independently validating its finding.
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_failed_ingest_source_candidate_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/failed.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Failed Fetch
description: A failed fetch record.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/dead"
ingest_status: failed
media: []
---

# Failed Fetch

Fetch failed after the recovery ladder.
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_source_with_existing_local_media_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    media = kb_root / "_media/images/2026/07/example-abcdef12.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"png")
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/with-media.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: With Media
description: A media-bearing source.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/media"
local_path: "knowledgebase/_media/images/2026/07/example-abcdef12.png"
media: []
---

# With Media

## Source-scoped Record

The source supplies the image as material associated with its stated claim.

## Evidence Boundary

The image provenance does not independently establish the source's asserted conclusion.

## Captured Media

![[knowledgebase/_media/images/2026/07/example-abcdef12.png|Example]]
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_tweet_thread_quote_video_shape_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/ai-literacy/sources/tweet-thread-video.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: ai-literacy
title: Tweet Thread with Quote and Video
description: A tweet/thread/video source candidate.
status: draft
tags: ["ai-literacy", "twitter", "video"]
sources: []
related: []
origin: "https://x.com/example/status/123"
media: []
---

# Tweet Thread with Quote and Video

## Source-scoped Record

The focal post, quoted post, reply thread, and embedded video artifact are preserved as source material.

## Evidence Boundary

The post and attached media are source claims, not independently verified performance evidence.

## Summary

The source combines the focal post, quoted post, reply thread, and embedded video artifact.

## Quoted Tweet

Quoted text is preserved in the same source note.

## Thread

1. First tweet.
2. Reply tweet.

## Video Transcript

Transcript text.

## Video — Slide OCR

evidence_quality: transcript-plus-visual
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_clean_media_description_candidate_passes(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    media = kb_root / "_media/images/2026/07/example-abcdef12.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"png")
    candidate = write_candidate(
        kb_root,
        "topics/example/notes/media/example-media.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: media-description
topic: example
title: Example Media
description: Media description.
status: draft
tags: []
sources: ["[[knowledgebase/topics/example/sources/with-media]]"]
related: []
media_id: "abcdef12"
media_kind: image
media_status: captured
local_path: "knowledgebase/_media/images/2026/07/example-abcdef12.png"
---

# Example Media

![[knowledgebase/_media/images/2026/07/example-abcdef12.png|Example]]

## OCR

Pending.

## Vision Description

Pending.
""",
    )

    assert validator.validate(candidate, kb_root) == []


def test_wiki_candidate_without_citations_warns_only(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/notes/concepts/uncited.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: concept
topic: example
title: Uncited
description: A warning-only wiki candidate.
status: draft
tags: []
related: []
---

# Uncited

This needs citations before promotion.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert severities(findings) == {"warn"}
    assert "wiki-note-no-citations" in codes(findings)
    assert "recommended-frontmatter" in codes(findings)


def test_source_without_sources_field_does_not_warn_as_uncited(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/source.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Source
description: A source candidate.
status: draft
tags: []
related: []
origin: "file:///tmp/source.pdf"
media: []
---

# Source

## Source-scoped Record

The origin field identifies the local source and the source reports its own material.

## Evidence Boundary

The source note is a provenance record rather than an independent assessment.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "wiki-note-no-citations" not in codes(findings)
    assert severities(findings) == {"warn"}
    assert codes(findings) == {"recommended-frontmatter"}


def test_blocks_candidate_outside_kb(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")

    findings = validator.validate(outside, kb_root)

    assert codes(findings) == {"candidate-outside-kb"}
    assert severities(findings) == {"block"}


def test_blocks_missing_required_source_fields(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/bad.md",
        """---
author: kb-bot
created: 2026-07-14
type: source
topic: example
---

No H1.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "source-required-frontmatter" in codes(findings)
    assert "missing-h1" in codes(findings)
    assert "block" in severities(findings)


def test_blocks_bad_link_forms_outside_code(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/bad-links.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Bad Links
description: Bad links.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/source"
---

# Bad Links

Bad: [[https://example.com]] and [[../relative-note]].
Ignored inline: `[[https://example.com]]`

```
[[../ignored-in-code]]
```
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "external-url-wikilink" in codes(findings)
    assert "relative-wikilink" in codes(findings)
    assert len([finding for finding in findings if finding.code == "external-url-wikilink"]) == 1


def test_blocks_invalid_ingest_status_and_evidence_quality(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/notes/media/video.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: media-description
topic: example
title: Video
description: A video note.
status: draft
tags: []
sources: []
related: []
ingest_status: maybe
evidence_quality: vibes
---

# Video
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "invalid-ingest-status" in codes(findings)
    assert "invalid-evidence-quality" in codes(findings)
    assert "block" in severities(findings)


def test_warns_for_missing_local_media_and_origin_collision(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    write_candidate(
        kb_root,
        "topics/example/sources/existing.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: Existing
description: Existing.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/same"
---

# Existing
""",
    )
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/new.md",
        """---
author: kb-bot
created: 2026-07-14
modified: 2026-07-14
type: source
topic: example
title: New
description: New.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/same"
local_path: "knowledgebase/_media/images/missing.png"
media: []
---

# New

## Source-scoped Record

The candidate identifies the source's material while exercising media and collision checks.

## Evidence Boundary

This provenance record does not establish any claim beyond the source itself.
""",
    )

    findings = validator.validate(candidate, kb_root)

    assert "missing-local-media" in codes(findings)
    assert "origin-collision" in codes(findings)
    assert severities(findings) == {"warn"}


def test_blocks_source_origin_that_is_governed_alias_for_existing_source(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    existing = write_candidate(
        kb_root,
        "topics/example/sources/existing.md",
        valid_source_text("Existing", "https://example.com/current"),
    )
    assert existing.exists()
    candidate = kb_root / "topics/example/sources/new.md"
    candidate_text = valid_source_text("New", "https://example.com/legacy")
    (kb_root / "_url-aliases.json").write_text(
        json.dumps(
            {
                "author": "kb-bot",
                "version": 1,
                "entries": [
                    {
                        "alias": "https://example.com/legacy",
                        "source_note": "topics/example/sources/existing.md",
                        "evidence": "manual redirect audit",
                        "verified": "2026-09-06",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    findings = validator.validate_text(candidate_text, candidate, kb_root, staged=True)

    assert "url-alias-collision" in codes(findings)
    assert "block" in severities(findings)


def test_invalid_alias_registry_is_a_blocking_validation_finding(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(kb_root, "topics/example/sources/new.md", valid_source_text())
    (kb_root / "_url-aliases.json").write_text(
        json.dumps({"author": "human", "version": 1, "entries": []}),
        encoding="utf-8",
    )

    findings = validator.validate(candidate, kb_root)

    assert "invalid-url-alias-registry" in codes(findings)
    assert "block" in severities(findings)


def test_source_requires_explicit_empty_media_manifest(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/no-media-key.md",
        """---
author: kb-bot
created: 2026-08-14
modified: 2026-08-14
type: source
topic: example
title: Missing Media Key
description: A source candidate without an explicit media inventory.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/no-media-key"
ingest_status: succeeded
---

# Missing Media Key

## Source-scoped Record

The source records one claim.

## Evidence Boundary

The source record does not independently establish the claim.
""",
    )

    assert "missing-source-media-manifest" in codes(validator.validate(candidate, kb_root))


def test_source_rejects_duplicate_media_ids(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    candidate = write_candidate(
        kb_root,
        "topics/example/sources/duplicate-media.md",
        """---
author: kb-bot
created: 2026-08-14
modified: 2026-08-14
type: source
topic: example
title: Duplicate Media
description: A source candidate with a repeated attachment hash.
status: draft
tags: []
sources: []
related: []
origin: "https://example.com/duplicate-media"
ingest_status: succeeded
media:
  - id: "same-id"
    kind: video
    status: not-captured
    origin_url: "https://example.com/video.mp4"
    license: unknown
    not_captured_reason: "not needed"
  - id: "same-id"
    kind: video
    status: not-captured
    origin_url: "https://example.com/video.mp4"
    license: unknown
    not_captured_reason: "duplicate attachment"
---

# Duplicate Media

## Source-scoped Record

The source records one claim and a repeated attachment.

## Evidence Boundary

The source record does not independently establish the claim.
""",
    )

    assert "duplicate-source-media-id" in codes(validator.validate(candidate, kb_root))


def test_media_description_rejects_id_owned_by_another_note(tmp_path: Path) -> None:
    validator = load_validator_module()
    kb_root = tmp_path / "knowledgebase"
    existing = write_candidate(
        kb_root,
        "topics/example/notes/media/existing.md",
        """---
author: kb-bot
created: 2026-08-14
modified: 2026-08-14
type: media-description
topic: example
title: Existing Media
description: Existing media record.
status: draft
tags: []
sources: []
related: []
media_id: "shared-id"
media_kind: image
media_status: not-captured
---

# Existing Media
""",
    )
    assert existing.exists()
    candidate = write_candidate(
        kb_root,
        "topics/other/notes/media/duplicate.md",
        """---
author: kb-bot
created: 2026-08-14
modified: 2026-08-14
type: media-description
topic: other
title: Duplicate Media
description: Duplicate media record.
status: draft
tags: []
sources: []
related: []
media_id: "shared-id"
media_kind: image
media_status: not-captured
---

# Duplicate Media
""",
    )

    assert "duplicate-media-description-id" in codes(validator.validate(candidate, kb_root))
    assert validator.main([str(candidate), "--kb-root", str(kb_root)]) == 2
