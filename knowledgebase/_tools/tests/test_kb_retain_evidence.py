import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('retain_evidence', Path(__file__).parents[1] / 'kb-retain-evidence.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def packet(tmp_path):
    source = tmp_path / 'staged.txt'
    source.write_text('Approved extraction: café\n')
    metadata = dict(origin='https://example.org/article', transport='https://example.org/article',
                    retrieved_at='2026-09-06T12:00:00Z', channel='excerpt', scope='excerpt',
                    limitations='Selected passage only; no images.', approval_ref='test approval',
                    retention_basis='permitted-excerpt')
    return tmp_path, source, metadata


def test_roundtrip_and_idempotence(packet):
    root, source, metadata = packet
    result = mod.retain(root, source, approved=True, **metadata)
    assert result['state'] == 'EVIDENCE_RETAINED'
    path = root / result['path']
    before = path.stat().st_mtime_ns
    assert mod.verify(root, path)['state'] == 'EVIDENCE_VERIFIED'
    assert mod.retain(root, source, approved=True, **metadata)['state'] == 'EVIDENCE_ALREADY_RETAINED'
    assert path.stat().st_mtime_ns == before


def test_different_retrieval_metadata_is_distinct(packet):
    root, source, metadata = packet
    first = mod.retain(root, source, approved=True, **metadata)
    metadata['retrieved_at'] = '2026-09-06T13:00:00Z'
    second = mod.retain(root, source, approved=True, **metadata)
    assert first['path'] != second['path']
    assert first['content_sha256'] == second['content_sha256']


def test_public_domain_basis_roundtrip_and_digests(packet):
    root, source, metadata = packet
    metadata.update(scope='supplied-extraction', channel='page-text', retention_basis='public-domain')

    result = mod.retain(root, source, approved=True, **metadata)
    path = root / result['path']
    record = json.loads(path.read_text())

    assert record['retention_basis'] == 'public-domain'
    assert result['content_sha256'] == mod.digest(source.read_bytes())
    assert result['sha256'] == mod.digest(path.read_bytes())
    assert mod.verify(root, path)['state'] == 'EVIDENCE_VERIFIED'


def test_permitted_excerpt_scope_restriction_is_unchanged(packet):
    root, source, metadata = packet
    metadata.update(scope='supplied-extraction', retention_basis='permitted-excerpt')

    with pytest.raises(ValueError, match='excerpt scope'):
        mod.retain(root, source, approved=True, **metadata)
    assert not (root / mod.STORE).exists()


@pytest.mark.parametrize('field,value', [('retrieved_at', '2026-09-06'), ('origin', 'https://user:secret@example.org'),
    ('channel', 'inference'), ('approval_ref', ''), ('limitations', ''), ('scope', 'complete'),
    ('retention_basis', 'unknown'), ('transport', 'https://example.org/\n')])
def test_invalid_metadata_writes_nothing(packet, field, value):
    root, source, metadata = packet
    metadata[field] = value
    with pytest.raises(ValueError):
        mod.retain(root, source, approved=True, **metadata)
    assert not (root / mod.STORE).exists()


@pytest.mark.parametrize('approved', [False, 'false', 1, [1], None])
def test_approval_required(packet, approved):
    root, source, metadata = packet
    with pytest.raises(ValueError, match='approval'):
        mod.retain(root, source, approved=approved, **metadata)


def test_tamper_detected_without_overwrite(packet):
    root, source, metadata = packet
    result = mod.retain(root, source, approved=True, **metadata)
    path = root / result['path']
    record = json.loads(path.read_text())
    record['limitations'] = 'tampered'
    path.write_bytes(mod.encoded(record))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='digest'):
        mod.verify(root, path)
    with pytest.raises(ValueError):
        mod.retain(root, source, approved=True, **metadata)
    assert path.read_bytes() == before


@pytest.mark.parametrize('part', ['input', 'directory', 'lock'])
def test_symlinks_rejected(packet, part):
    root, source, metadata = packet
    if part == 'input':
        linked = root / 'linked.txt'
        linked.symlink_to(source)
        source = linked
    elif part == 'directory':
        (root / '_tools').mkdir()
        (root / '_tools/evidence').symlink_to(root / 'missing', target_is_directory=True)
    else:
        (root / '.kb-write.lock').symlink_to(root / 'missing')
    with pytest.raises(ValueError, match='symlink'):
        mod.retain(root, source, approved=True, **metadata)


def test_retain_rejects_symlinked_kb_root_before_following_source(packet):
    root, source, metadata = packet
    alias = root.parent / f'{root.name}-alias'
    alias.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match='symlink'):
        # Use the real source path to prove the supplied root itself is the
        # rejected boundary, rather than relying on a second path check.
        mod.retain(alias, source, approved=True, **metadata)
    assert not (root / mod.STORE).exists()


def test_verify_rejects_symlinked_kb_root_before_following_artifact(packet):
    root, source, metadata = packet
    result = mod.retain(root, source, approved=True, **metadata)
    path = root / result['path']
    alias = root.parent / f'{root.name}-alias'
    alias.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match='symlink'):
        mod.verify(alias, path)


def test_outside_input_rejected(packet):
    root, source, metadata = packet
    with pytest.raises(ValueError):
        mod.retain(root, root / '../external', approved=True, **metadata)


def test_size_limit(packet):
    root, source, metadata = packet
    source.write_bytes(b'x' * (mod.MAX_BYTES + 1))
    with pytest.raises(ValueError, match='2 MiB'):
        mod.retain(root, source, approved=True, **metadata)


def test_fifo_input_rejected_without_blocking(packet):
    root, source, metadata = packet
    fifo = root / 'pipe'
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular file'):
        mod.retain(root, fifo, approved=True, **metadata)


def test_hard_link_rejected(packet):
    root, source, metadata = packet
    linked = root / 'linked.txt'
    os.link(source, linked)
    with pytest.raises(ValueError, match='hard-linked'):
        mod.retain(root, linked, approved=True, **metadata)


def test_hard_linked_lock_rejected_without_publication(packet):
    root, source, metadata = packet
    marker = root.parent / 'lock-marker'
    marker.write_text('keep')
    os.link(marker, root / '.kb-write.lock')

    with pytest.raises(ValueError, match='singly linked'):
        mod.retain(root, source, approved=True, **metadata)
    assert marker.read_text() == 'keep'
    assert not (root / mod.STORE).exists()


def test_publication_fsyncs_evidence_directory(packet, monkeypatch):
    root, source, metadata = packet
    real_fsync = mod.os.fsync
    directory_fsyncs = []

    def record_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_fsyncs.append(True)
        return real_fsync(fd)

    monkeypatch.setattr(mod.os, 'fsync', record_fsync)
    result = mod.retain(root, source, approved=True, **metadata)

    assert result['state'] == 'EVIDENCE_RETAINED'
    assert directory_fsyncs


def test_deep_json_is_controlled_error(packet):
    root, _, _ = packet
    directory = root / mod.STORE
    directory.mkdir(parents=True)
    raw = b'[' * 10000 + b']' * 10000
    path = directory / (mod.digest(raw) + '.json')
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        mod.verify(root, path)


def test_metadata_size_limit(packet):
    root, source, metadata = packet
    metadata['approval_ref'] = 'x' * 8193
    with pytest.raises(ValueError, match='8192'):
        mod.retain(root, source, approved=True, **metadata)
