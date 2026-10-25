"""Synthetic review records for offline fixtures, never evidence of a real review."""
import hashlib
import json


def make_review(root, text, *, candidate="topics/example/sources/new.md",
                queue="topics/example/reading-list.md", locator="https://example.com/article",
                **overrides):
    record = {
        "author": "kb-bot", "version": 1,
        "candidate_path": candidate,
        "candidate_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "queue_path": queue, "primary_locator": locator,
        "evidence": [],
        "coverage": {"status": "partial", "covered": ["Synthetic fixture text"],
                     "omissions": ["No external source examined in this offline test"]},
        "reproducible_snapshot": False,
        "retention_limitations": "Synthetic test only; no retrieved source retained.",
        "writer_ref": "fixture-writer", "reviewer_ref": "fixture-reviewer",
        "reviewed_at": "2026-09-07T12:00:00Z",
        "disposition": "publish-with-boundary",
    }
    record.update(overrides)
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path = root / "_tools" / "reviews" / (hashlib.sha256(raw).hexdigest() + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path
