#!/usr/bin/env python3
"""Offline, bounded retention of explicitly approved UTF-8 extraction evidence.

No fetching, source publication, queue completion, or model calls. A digest
establishes byte identity, not authenticity, completeness, or factual accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit

MAX_BYTES = 2 * 1024 * 1024
STORE = Path('_tools/evidence')
CHANNELS = {'page-text', 'mirror-json', 'ocr', 'transcript', 'excerpt'}
# Retention basis is an explicit operator assertion.  These values are labels
# only; validation does not infer a license or public-domain status from URLs.
BASES = {'user-provided', 'open-license', 'permitted-excerpt', 'public-domain'}


def _load_write_guard():
    key = 'kb_retain_evidence_write_guard'
    module = sys.modules.get(key)
    if module is not None:
        return module
    script = Path(__file__).with_name('kb-write-guard.py')
    spec = importlib.util.spec_from_file_location(key, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load shared KB write guard: {script}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


_WRITE_GUARD = _load_write_guard()


def canonical_root(root: Path | str) -> Path:
    """Validate a supplied KB root without following symlink components."""
    try:
        return _WRITE_GUARD.canonical_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f'unsafe KB root: {exc}') from exc


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def scoped(root: Path, path: Path) -> Path:
    """Reject symlinks in any component, including dangling destination links."""
    path = path if path.is_absolute() else root / path
    relative = path.relative_to(root)
    if '..' in relative.parts:
        raise ValueError('parent traversal is not allowed')
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('symlink paths are not allowed')
    path.resolve().relative_to(root)
    return path


def locator(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in {'http', 'https'} or not parts.hostname
            or parts.username or parts.password or any(c.isspace() for c in value)):
        raise ValueError('origin/transport must be HTTP(S) URLs without credentials or whitespace')
    return value


def read_regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('evidence input must be a regular file')
        if info.st_nlink != 1:
            raise ValueError('hard-linked input is not allowed')
        return handle.read(limit)


def fsync_directory(path: Path) -> None:
    """Durably flush directory metadata after publishing a new artifact."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('evidence store must be a directory')
        os.fsync(fd)
    finally:
        os.close(fd)


def validate(record: dict) -> None:
    fields = {'author', 'version', 'origin', 'transport', 'retrieved_at', 'channel',
              'scope', 'limitations', 'approval_ref', 'retention_basis', 'content',
              'content_sha256', 'content_bytes'}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError('invalid evidence fields')
    if record['author'] != 'kb-bot' or type(record['version']) is not int or record['version'] != 1:
        raise ValueError('invalid evidence ownership/version')
    for field in fields - {'version', 'content_bytes'}:
        if not isinstance(record[field], str):
            raise ValueError(f'{field} must be text')
        if field != 'content' and len(record[field]) > 8192:
            raise ValueError(f'{field} metadata exceeds 8192 characters')
    locator(record['origin'])
    locator(record['transport'])
    date = datetime.fromisoformat(record['retrieved_at'].replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('retrieved_at needs a timezone')
    if record['channel'] not in CHANNELS or record['scope'] not in {'excerpt', 'supplied-extraction'}:
        raise ValueError('invalid channel/scope')
    if record['retention_basis'] not in BASES:
        raise ValueError('invalid retention basis')
    if record['retention_basis'] == 'permitted-excerpt' and record['scope'] != 'excerpt':
        raise ValueError('permitted-excerpt retention requires excerpt scope')
    if not record['approval_ref'].strip() or not record['limitations'].strip():
        raise ValueError('approval reference and limitations are required')
    payload = record['content'].encode('utf-8')
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('evidence must contain 1 byte to 2 MiB of UTF-8 text')
    if type(record['content_bytes']) is not int or record['content_bytes'] != len(payload):
        raise ValueError('content byte count mismatch')
    if record['content_sha256'] != digest(payload):
        raise ValueError('content digest mismatch')


def verify(root: Path, path: Path) -> dict:
    root = canonical_root(root)
    path = scoped(root, path)
    if path.parent != root / STORE or not re.fullmatch(r'[0-9a-f]{64}\.json', path.name):
        raise ValueError('not a canonical evidence path')
    raw = read_regular(path, MAX_BYTES * 6 + 16385)
    if len(raw) > MAX_BYTES * 6 + 16384:
        raise ValueError('evidence envelope too large')
    try:
        record = json.loads(raw)
    except RecursionError as exc:
        raise ValueError('evidence JSON nesting is excessive') from exc
    validate(record)
    if raw != encoded(record) or digest(raw) != path.stem:
        raise ValueError('evidence envelope digest/encoding mismatch')
    return {'state': 'EVIDENCE_VERIFIED', 'path': str(path.relative_to(root)),
            'sha256': path.stem, 'content_sha256': record['content_sha256']}


def retain(root: Path, source: Path, *, approved: bool, **metadata) -> dict:
    if approved is not True:
        raise ValueError('explicit retention approval is required')
    root = canonical_root(root)
    source = scoped(root, source)
    # This is a KB path boundary, not a secret detector. Operators must still
    # check privacy and retention authority for the supplied input.
    payload = read_regular(source, MAX_BYTES + 1)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('input must contain 1 byte to 2 MiB')
    record = dict(metadata, author='kb-bot', version=1,
                  content=payload.decode('utf-8'), content_sha256=digest(payload),
                  content_bytes=len(payload))
    validate(record)
    raw = encoded(record)
    if len(raw) > MAX_BYTES * 6 + 16384:
        raise ValueError('evidence envelope too large')
    lock = scoped(root, Path('.kb-write.lock'))
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('KB lock must be a regular file')
        if info.st_nlink != 1:
            raise ValueError('KB lock must be singly linked')
        fcntl.flock(handle, fcntl.LOCK_EX)
        directory = scoped(root, STORE)
        directory.mkdir(parents=True, exist_ok=True)
        target = scoped(root, STORE / (digest(raw) + '.json'))
        if target.exists():
            result = verify(root, target)
            return dict(result, state='EVIDENCE_ALREADY_RETAINED')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.evidence-', delete=False) as out:
                temporary = Path(out.name)
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            # Atomic no-clobber publication: a racing existing file wins, never
            # an overwrite. Temporary cleanup cannot delete a retained artifact.
            os.link(temporary, target)
            fsync_directory(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        result = verify(root, target)
        return dict(result, state='EVIDENCE_RETAINED')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kb-root', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input', type=Path)
    group.add_argument('--verify', type=Path)
    parser.add_argument('--retain-approved', action='store_true')
    for name in ('origin', 'transport', 'retrieved-at', 'channel', 'scope',
                 'limitations', 'approval-ref', 'retention-basis'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify(args.kb_root, args.verify)
        else:
            metadata = {key: getattr(args, key) for key in (
                'origin', 'transport', 'retrieved_at', 'channel', 'scope',
                'limitations', 'approval_ref', 'retention_basis')}
            result = retain(args.kb_root, args.input, approved=args.retain_approved, **metadata)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({'state': 'STOP', 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
