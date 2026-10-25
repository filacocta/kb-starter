---
author: kb-bot
created: 2026-09-11
modified: 2026-09-13
type: meta
title: kb-media-inspect CLI Contract
description: Local-only bounded inspection of one approved staged image or video into private scratch evidence.
---

# kb-media-inspect.py — CLI Contract

`kb-media-inspect.py` is a temporary, local-only evidence-preparation helper.
It does not fetch URLs, call an API, invoke `yt-dlp`, download a model, publish
a source, create a media-description note, or interpret the media. The caller
must first use the shared `kb-media-capture.py --stage-dir` flow to place the
exact approved bytes under `_tools/staging/media/` and must supply the full
lowercase SHA-256 and canonical origin URL.

The helper writes only to a new child directory under
`_tools/staging/media/`. The staging tree is private scratch and should be
ignored by Git. The original staged input is never copied, changed, or removed
by inspection. A `receipt.json` is written last and remains after cleanup as a
bot-owned custody record; its `created_files` list contains every derived file,
not the receipt itself. Before publishing that receipt, the helper rechecks the
original input's inode, size, and mtime and rehashes its bounded bytes against
the supplied SHA-256, so a same-size byte change with restored metadata cannot
be reported as complete.

## Inspect one staged asset

```bash
_tools/.venv/bin/python _tools/kb-media-inspect.py \
  --kb-root <kb-root> \
  --input _tools/staging/media/<sha256>.mp4 \
  --input-sha256 <64-lowercase-hex> \
  --origin-url https://example.example/post/asset \
  --output-dir _tools/staging/media/inspection-2026-09-11/R08-extraction \
  --run-id inspection-2026-09-11 --asset-id R08 \
  --max-frames 40 \
  --interval-seconds 5 \
  --timeout-seconds 600
```

Images are checked and recorded without producing a derivative image; inspect
the original staged image with the native image viewer when needed. For a
video, `ffprobe` checks that a finite video duration is at most 180 seconds and
that a video stream exists. `ffmpeg` then extracts regularly spaced,
timestamped JPEGs, capped at 40 frames and scaled to no more than 1920×1080.
The decoder is invoked with `-nostdin` and
`-protocol_whitelist file,pipe`; no network protocol is allowed. The shared
timeout is a wall-clock budget for probing and extraction (at most 600 seconds
by default).

Optional extraction flags are deliberately explicit and video-only. Image
inputs with `--ocr` or `--asr` are rejected before creating output or a receipt;
image custody checks must not claim completion of unsupported extraction.

```bash
# Run local Tesseract over the extracted frames.
... --ocr

# Use only the existing local Systran/faster-whisper-base cache.
... --asr
```

ASR always constructs faster-whisper with `local_files_only=True`,
`device="cpu"`, and `compute_type="int8"`. A missing audio stream produces no
transcript and is recorded as `skipped-no-audio`; the tool never invents an
audio channel. A missing model cache or an extractor timeout is recorded as a
partial/failed terminal state, not silently substituted with a network model or
another provider. Returned segments pass a deterministic quality gate before
they can be marked usable: timestamps must satisfy
`0 <= start_seconds < end_seconds <= duration_seconds + 1s`, and speech must
contain non-punctuation text. Any rejected segments are reported in
`asr.quality.reasons`, and the inspection becomes partial/unusable rather than
claiming a complete ASR result. A private `transcript.json` still preserves
the raw engine segments when validation fails, explicitly labels them
`unvalidated-excluded`, and may include the separately usable subset. Repeated
text normally produces a warning only; repetition is not treated as ground
truth (for example, a song may repeat a phrase). The narrow degenerate-pattern
check additionally excludes a near/all-dominating group of at least three tiny
one- or two-token segments, such as five repeated `You` segments. This is a
quality flag for review, not a semantic claim about the recording.

When `--ocr` is requested, each extracted frame is retained in `ocr.json` with
`status: "ok"` and its text, or with `status: "failed"`/`"skipped"` and bounded
error metadata. A nonzero Tesseract exit therefore cannot disappear from the
receipt: all failed frames produce OCR state `failed`, a mixture of successful
and failed frames produces `partial`, and the inspection is partial/failed
rather than complete. Successful OCR with no non-whitespace text is distinct:
the OCR state is `ok-no-text` and the inspection may complete. The receipt
contains only bounded frame counts and failure metadata; OCR text remains in
the private artifact, never in the receipt.

## Receipt

The receipt is JSON (not a source note) with `author: "kb-bot"` and
`version: 1`. Its important fields are:

```json
{
  "author": "kb-bot",
  "version": 1,
  "tool": "kb-media-inspect",
  "state": "INSPECTION_COMPLETE",
  "run_id": "inspection-2026-09-11",
  "asset_id": "R08",
  "origin_url": "https://example.example/post/asset",
  "input": {
    "path": "_tools/staging/media/<sha256>.mp4",
    "sha256": "<full input hash>",
    "size": 123456,
    "kind": "video",
    "duration_seconds": 12.3,
    "has_audio": false
  },
  "sampling": {
    "method": "regular-interval",
    "requested_max_frames": 40,
    "interval_seconds": 5.0,
    "max_frame_width": 1920,
    "max_frame_height": 1080,
    "timestamps_seconds": [0.0, 5.0],
    "frames_created": 2
  },
  "created_files": [
    {
      "path": "_tools/staging/media/inspection-2026-09-11/R08-extraction/frame-001-000000.000s.jpg",
      "sha256": "<derived hash>",
      "size": 9876
    }
  ],
  "coverage": {
    "status": "partial",
    "covered": ["ffprobe video stream and finite duration", "regularly sampled timestamped frames"],
    "omissions": ["visual content between sampled timestamps", "continuous motion/audio synchronization"]
  },
  "cleanup": {
    "state": "not-requested",
    "required_disposition": null,
    "reviewed_disposition": null,
    "review_ref": null,
    "removed_files": [],
    "already_absent": [],
    "staged_input_deleted": false
  },
  "evidence_limitations": [
    "Temporary local extraction only; no source publication or media-description note is created.",
    "No model interpretation is performed by this tool."
  ],
  "sampling_limitations": [
    "Regularly sampled frames do not establish continuous visual coverage."
  ]
}
```

For an OCR request, `ocr.state` is `ok` when every frame succeeds and at least
one frame has recognized text, `ok-no-text` when every frame succeeds but all
texts are empty, `partial` when at least one frame succeeds and one fails, or
`failed` when no frame succeeds. `ocr.frames_succeeded`,
`ocr.frames_failed`, `ocr.recognized_text_frames`, and bounded `ocr.failures`
summarize the run without copying OCR text into the receipt.

For an ASR request, `asr.quality` records the known clip duration, one-second
timestamp tolerance, usable/excluded counts, deterministic reasons, and any
repetition warnings. Transcript speech is never embedded in the receipt.

`INSPECTION_COMPLETE` means the requested local extraction completed. A
`INSPECTION_PARTIAL` or `INSPECTION_FAILED` receipt is still useful for
resumption, but does not establish complete visual/audio evidence and is not
eligible for ordinary cleanup; the narrowly reviewed unusable-ASR disposition
below is the only exception. Receipt
paths, input paths, and manifest paths are KB-root-relative; the manifest hash
and size are checked again by cleanup. Digests establish byte identity only,
not source authenticity, factual truth, rights, or full coverage.

## Verified cleanup

Cleanup is never automatic. After an independent review and source
disposition, remove only the exact files listed by a receipt. A successful
cleanup changes the receipt state to `CLEANUP_COMPLETE` and records the
disposition while retaining the receipt:

```bash
_tools/.venv/bin/python _tools/kb-media-inspect.py \
  --kb-root <kb-root> \
  --cleanup-receipt _tools/staging/media/inspection-2026-09-11/R08-extraction/receipt.json
```

An otherwise completed extraction whose only terminal issue is the deterministic
unusable-ASR quality gate may be cleaned after an explicit reviewed
disposition. Supply the exact existing KB-relative review record as well as the
disposition; this does not authorize cleanup of decoder failures, timeouts,
missing caches, changed inputs, or any other partial/interrupted run:

```bash
... --cleanup-receipt .../receipt.json \
  --reviewed-disposition unusable-asr \
  --review-ref _tools/staging/media-sources-2026-09-11/independent-media-review.md
```

The review reference is checked as an existing regular, singly linked KB file,
must not point anywhere under the media inspection scratch tree (including a
sibling run's receipt), and is not modified.
Cleanup records both the disposition and reference in the receipt. Repeating
the cleanup remains idempotent without resupplying the review options.

To remove the staged input as well, name that one exact path explicitly. The
tool requires that it match the receipt's input path and hash and rechecks its
bytes immediately before unlinking:

```bash
... --cleanup-receipt .../receipt.json \
  --delete-staged-input _tools/staging/media/<sha256>.mp4
```

Cleanup refuses ordinary incomplete inspection receipts, changed hashes or sizes,
symlinks, hardlinks, special files, extra/unlisted files, traversal, output
directories outside the private staging tree, a receipt in the wrong
directory, and broad directory deletion.
It never deletes the receipt, input, or any unlisted path by default. Repeating
cleanup after a successful run is safe and reports manifest files as
`already_absent`; an unexpected or changed file is a hard stop. Failed or
interrupted inspection may leave safe unresolved scratch files; inspect the
receipt and tree before retrying or cleaning them.

## Boundaries

- Input must be a singly linked regular image/video file under the selected
  KB's `_tools/staging/media/` directory and no larger than 25 MiB.
- Video duration must be finite, positive, and no more than 180 seconds.
- At most 40 frames are requested; each output is bounded to 1920×1080 and
  the overall ffprobe/ffmpeg/optional-extraction budget is at most 600 seconds.
- No HLS playlist, poster, alternate rendition, companion URL, login/DRM
  workaround, browser fetch, source publication, or durable `_media/` copy is
  performed here.
- The tool is cooperative same-user protection, not a defense against a
  malicious process racing the filesystem after validation. It fails closed
  on ordinary path and byte-integrity violations.
