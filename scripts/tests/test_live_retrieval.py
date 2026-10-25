"""Observable retrieval behavior on independent synthetic vaults."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'kb-search'


def note(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def invoke(root, *args, env=None):
    settings = {k: v for k, v in os.environ.items()
                if k not in {'KB_ROOT', 'KB_CONFIG', 'KB_CONFIG_PATH', 'KB_AGENT', 'KB_PERSONAL_ROOT', 'KB_WORK_ROOT'}}
    settings.update(env or {})
    command = [sys.executable, str(SCRIPT)]
    if root is not None:
        command += ['--kb-root', str(root)]
    return subprocess.run([*command, *args], capture_output=True, text=True, env=settings)


def test_finds_current_body_facts_without_frontmatter_or_index(tmp_path):
    path = note(tmp_path, 'topics/demo/notes/plain.md', '# Generic title\n\nThe zephyr coefficient is seven.\n')
    before = path.read_bytes()
    result = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'zephyr', '--json')
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    found = payload['results'][0]
    assert found['path'] == 'topics/demo/notes/plain.md'
    assert 'zephyr coefficient' in found['text']
    assert found['sha256'] == hashlib.sha256(before).hexdigest()
    assert found['line_start'] <= 3 <= found['line_end']
    assert path.read_bytes() == before
    assert not (tmp_path / '_kb-index.json').exists()


def test_sources_are_opt_in_and_staging_is_excluded(tmp_path):
    note(tmp_path, 'topics/demo/sources/original.md', '# Evidence\nuniqueoriginal\n')
    note(tmp_path, 'topics/demo/notes/staging/draft.md', '# Draft\nuniquestaged\n')
    normal = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'uniqueoriginal', '--json')
    assert normal.returncode == 1
    expanded = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'uniqueoriginal', '--sources', '--json')
    assert expanded.returncode == 0
    assert json.loads(expanded.stdout)['results'][0]['role'] == 'source'
    noise = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'uniquestaged', '--sources', '--json')
    assert noise.returncode == 1


def test_context_contains_the_requested_summary(tmp_path):
    note(tmp_path, 'topics/demo/_summary.md', '---\nauthor: kb-bot\n---\n# Demo\nA disputed observation.\n')
    note(tmp_path, 'topics/other/_summary.md', '# Other\nUnrelated context.\n')
    result = invoke(tmp_path, '--domain', 'personal', '--context', '--topic', 'demo', '--json')
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['results'][0]['path'] == 'topics/demo/_summary.md'
    assert 'disputed' in payload['results'][0]['text']
    assert all('/other/' not in item['path'] for item in payload['results'])


@pytest.mark.parametrize('json_output', [False, True])
def test_utf8_budget_includes_citations_and_valid_output(tmp_path, json_output):
    note(tmp_path, 'topics/demo/notes/long.md', '# Needle\n' + '漢字' * 5000)
    args = ['--domain', 'personal', '--body', '-q', 'needle', '--budget-bytes', '1024']
    result = invoke(tmp_path, *args, *(['--json'] if json_output else []))
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.encode()) <= 1024
    if json_output:
        assert json.loads(result.stdout)['results'][0]['clipped'] is True
    else:
        assert 'partial excerpt' in result.stdout


def test_section_read_preserves_nested_headings_and_ignores_code_fences(tmp_path):
    note(tmp_path, 'topics/demo/notes/sections.md', '# One\nkeep\n```md\n# Fake\n```\n## Child\nkeep child\n# Two\nexclude\n')
    result = invoke(tmp_path, '--domain', 'personal', '--read', 'topics/demo/notes/sections.md', '--heading', 'One', '--json')
    assert result.returncode == 0, result.stderr
    passage = json.loads(result.stdout)['results'][0]
    assert passage['line_start'] == 1 and passage['line_end'] == 7
    assert 'keep child' in passage['text'] and 'exclude' not in passage['text']


def test_old_digest_cannot_be_used_to_read_changed_content(tmp_path):
    path = note(tmp_path, 'topics/demo/notes/note.md', '# Old\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text('# Changed\n')
    result = invoke(tmp_path, '--domain', 'personal', '--read', 'topics/demo/notes/note.md', '--expect-sha256', digest)
    assert result.returncode == 2 and 'changed' in result.stderr
    assert not result.stdout


def test_wrong_domain_does_not_fall_back_to_generic_root(tmp_path):
    note(tmp_path, 'topics/demo/notes/private.md', '# Private needle\n')
    result = invoke(None, '--domain', 'work', '--body', '-q', 'needle', env={
        'KB_ROOT': str(tmp_path), 'CODEX_HOME': str(tmp_path / 'no-codex'), 'CLAUDE_HOME': str(tmp_path / 'no-claude')})
    assert result.returncode == 2 and 'no work KB root' in result.stderr
    assert 'Private needle' not in result.stdout


def test_domain_selects_only_its_named_configuration_root(tmp_path):
    personal = tmp_path / 'personal'
    work = tmp_path / 'work'
    note(personal, 'topics/demo/notes/note.md', '# Needle\nPrivate personal content\n')
    note(work, 'projects/team/notes/note.md', '# Needle\nWork content\n')
    config = tmp_path / 'roots.conf'
    config.write_text(f'KB_PERSONAL_ROOT: {personal}\nKB_WORK_ROOT: {work}\n')
    result = invoke(None, '--domain', 'work', '--config', str(config), '--body', '-q', 'needle', '--json')
    assert result.returncode == 0, result.stderr
    assert 'Work content' in result.stdout and 'Private personal' not in result.stdout
    assert json.loads(result.stdout)['root'] == str(work)


def test_transplanted_metadata_index_is_rejected(tmp_path):
    root = tmp_path / 'kb'
    root.mkdir()
    (root / '_kb-index.json').write_text(json.dumps({'kb_root': '/some/other/vault', 'records': []}))
    result = invoke(root, '--query', 'needle')
    assert result.returncode == 2 and 'root' in result.stderr


def test_archived_evidence_needs_both_permissions(tmp_path):
    note(tmp_path, 'topics/demo/sources/archive/old.md', '# Old\nretained evidence\n')
    path = 'topics/demo/sources/archive/old.md'
    refused = invoke(tmp_path, '--domain', 'personal', '--read', path, '--include-excluded')
    assert refused.returncode == 2 and 'source evidence' in refused.stderr
    accepted = invoke(tmp_path, '--domain', 'personal', '--read', path, '--include-excluded', '--sources')
    assert accepted.returncode == 0 and 'retained evidence' in accepted.stdout


def test_literal_hash_and_indented_headings(tmp_path):
    note(tmp_path, 'topics/demo/notes/headings.md', '# C#\nkeep language\n  ## Indented\nkeep child\n# Next ###\nother\n')
    result = invoke(tmp_path, '--domain', 'personal', '--read', 'topics/demo/notes/headings.md', '--heading', 'C#', '--json')
    assert result.returncode == 0, result.stderr
    assert 'keep child' in json.loads(result.stdout)['results'][0]['text']


def test_work_search_covers_arbitrary_collections_without_repeating_identical_evidence(tmp_path):
    text = '# Policy\nThe cobalt design has a bounded evidence scope.\n'
    note(tmp_path, 'projects/widget/notes/policy.md', text)
    note(tmp_path, 'docs/policy.md', text)
    result = invoke(tmp_path, '--domain', 'work', '--body', '-q', 'cobalt', '--json')
    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)['results']
    assert len(records) == 1
    assert len(records[0]['equivalent_paths']) == 1
    assert {records[0]['path'], records[0]['equivalent_paths'][0]} == {
        'projects/widget/notes/policy.md', 'docs/policy.md'}


def test_work_project_scope_does_not_expand_sibling_collections(tmp_path):
    note(tmp_path, 'projects/widget/notes/design.md', '# Cobalt\nScoped design.\n')
    note(tmp_path, 'projects/other/notes/design.md', '# Cobalt\nOther project.\n')
    note(tmp_path, 'docs/design.md', '# Cobalt\nShared design.\n')
    result = invoke(tmp_path, '--domain', 'work', '--body', '--project', 'widget', '-q', 'cobalt', '--json')
    assert result.returncode == 0, result.stderr
    assert [p['path'] for p in json.loads(result.stdout)['results']] == ['projects/widget/notes/design.md']


@pytest.mark.parametrize('path', ['_context-map.md', 'legacy-share/knowledge/_context-map.md'])
def test_work_context_uses_generic_or_legacy_nested_map_without_broad_reading(tmp_path, path):
    note(tmp_path, path, '# Team\nNavigation and uncertain guidance.\n')
    note(tmp_path, 'docs/unrelated.md', '# Unrelated\nDo not preload me.\n')
    result = invoke(tmp_path, '--domain', 'work', '--context', '--json')
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [p['path'] for p in payload['results']] == [path]
    assert payload['files_read'] == 1


def test_work_context_map_selection_is_explicit_on_ambiguity(tmp_path):
    note(tmp_path, 'first/_context-map.md', '# First\nNavigation.\n')
    note(tmp_path, 'second/_context-map.md', '# Second\nNavigation.\n')
    ambiguous = invoke(tmp_path, '--domain', 'work', '--context', '--json')
    assert ambiguous.returncode == 2 and not ambiguous.stdout
    assert 'multiple context maps' in ambiguous.stderr
    assert invoke(tmp_path, '--domain', 'work', '--body', '-q', 'navigation').returncode == 0
    note(tmp_path, '_context-map.md', '# Selected root\nOwner-selected navigation.\n')
    preferred = invoke(tmp_path, '--domain', 'work', '--context', '--json')
    assert preferred.returncode == 0, preferred.stderr
    assert [p['path'] for p in json.loads(preferred.stdout)['results']] == ['_context-map.md']


def test_scoped_work_context_never_selects_another_projects_map(tmp_path):
    note(tmp_path, 'projects/widget/_summary.md', '# Widget\nScoped orientation.\n')
    note(tmp_path, 'projects/other/_context-map.md', '# Other project\nDo not select me.\n')
    first = invoke(tmp_path, '--domain', 'work', '--context', '--project', 'widget', '--json')
    assert first.returncode == 0, first.stderr
    assert [p['path'] for p in json.loads(first.stdout)['results']] == ['projects/widget/_summary.md']
    note(tmp_path, 'team-share/_context-map.md', '# Shared\nGlobal navigation.\n')
    shared = invoke(tmp_path, '--domain', 'work', '--context', '--project', 'widget', '--json')
    assert shared.returncode == 0, shared.stderr
    assert json.loads(shared.stdout)['results'][0]['path'] == 'team-share/_context-map.md'
    note(tmp_path, 'projects/widget/_context-map.md', '# Widget\nSelected project navigation.\n')
    scoped = invoke(tmp_path, '--domain', 'work', '--context', '--project', 'widget', '--json')
    assert scoped.returncode == 0, scoped.stderr
    paths = [p['path'] for p in json.loads(scoped.stdout)['results']]
    assert paths == ['projects/widget/_context-map.md', 'projects/widget/_summary.md']


@pytest.mark.parametrize('count,limit', [(6, 5), (50, 1)])
def test_result_limits_report_known_omissions_and_candidate_pruning(tmp_path, count, limit):
    for index in range(count):
        note(tmp_path, f'docs/note-{index}.md', f'# Needle {index}\nDistinct evidence {index}.\n')
    result = invoke(tmp_path, '--domain', 'work', '--body', '-q', 'needle', '--limit', str(limit), '--json')
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload['results']) == limit
    assert payload['scan_complete'] is True and payload['result_limit_reached'] is True
    assert 1 <= payload['omitted_results'] <= count - limit


def test_work_context_discovery_prunes_archives_sources_and_hidden_settings(tmp_path):
    note(tmp_path, 'docs/_context-map.md', '# Selected\nTeam guidance.\n')
    for path in ['archive/_context-map.md', '_tools/_context-map.md', '.settings/_context-map.md',
                 'topics/demo/sources/_context-map.md']:
        note(tmp_path, path, '# Excluded\nDo not choose me.\n')
    result = invoke(tmp_path, '--domain', 'work', '--context', '--json')
    assert result.returncode == 0, result.stderr
    assert [p['path'] for p in json.loads(result.stdout)['results']] == ['docs/_context-map.md']


def test_work_context_does_not_fall_back_from_unsafe_root_map(tmp_path):
    note(tmp_path, 'legacy/_context-map.md', '# Nested\nDo not silently prefer me.\n')
    (tmp_path / '_context-map.md').symlink_to(tmp_path / 'missing.md')
    result = invoke(tmp_path, '--domain', 'work', '--context', '--json')
    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['scan_complete'] is False
    assert all(p['path'] != 'legacy/_context-map.md' for p in payload['results'])


def test_ordinary_work_search_excludes_governance_but_explicit_read_is_available(tmp_path):
    note(tmp_path, 'AGENTS.md', '# Instructions\npolicyneedle\n')
    assert invoke(tmp_path, '--domain', 'work', '--body', '-q', 'policyneedle').returncode == 1
    result = invoke(tmp_path, '--domain', 'work', '--read', 'AGENTS.md', '--include-excluded')
    assert result.returncode == 0 and 'policyneedle' in result.stdout


def test_work_audit_inventories_arbitrary_document_collections(tmp_path):
    note(tmp_path, 'docs/policy.md', '# Cobalt policy\nSame retained text.\n')
    note(tmp_path, 'reference/policy.md', '# Cobalt policy\nSame retained text.\n')
    result = subprocess.run([sys.executable, str(SCRIPT.with_name('kb-audit')), '--domain', 'work',
                             '--kb-root', str(tmp_path), '--json'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['counts']['files'] == 2 and payload['duplicate_groups'] == 1


def test_directory_read_failure_is_not_complete_absence(tmp_path):
    path = note(tmp_path, 'topics/demo/hidden-facts/note.md', '# Needle\nneedle\n').parent
    path.chmod(0)
    try:
        result = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'needle', '--json')
        assert result.returncode == 3, result.stderr
        assert json.loads(result.stdout)['scan_complete'] is False
    finally:
        path.chmod(0o700)


def test_invalid_utf8_consumes_scan_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    sys.path.insert(0, str(SCRIPT.parent))
    import kb_retrieval
    directory = tmp_path / 'topics/demo/notes'
    directory.mkdir(parents=True)
    for index in range(10):
        (directory / f'{index}.md').write_bytes(b'\xff' * 8)
    monkeypatch.setattr(kb_retrieval, 'MAX_SCAN_BYTES', 64)
    monkeypatch.setattr(kb_retrieval, 'MAX_FILE_BYTES', 8)
    args = SimpleNamespace(topic='demo', project=None, query='needle', context=False,
                           domain='personal', limit=5, sources=False)
    passages, metadata = kb_retrieval.retrieve(tmp_path, args)
    assert not passages
    assert metadata['bytes_attempted'] == 64 and metadata['bytes_read'] == 0
    assert not metadata['scan_complete']


def test_unsafe_paths_are_rejected_without_following_links(tmp_path):
    outside = note(tmp_path, 'outside.md', '# Outside\n')
    root = tmp_path / 'kb'
    (root / 'topics/demo/notes').mkdir(parents=True)
    (root / 'topics/demo/notes/link.md').symlink_to(outside)
    for path in ['../outside.md', 'topics/demo/notes/link.md']:
        result = invoke(root, '--domain', 'personal', '--read', path)
        assert result.returncode == 2
        assert '# Outside' not in result.stdout


def test_unreadable_content_makes_search_incomplete_not_absent(tmp_path):
    path = note(tmp_path, 'topics/demo/notes/bad.md', '# Placeholder\n')
    path.write_bytes(b'\xff')
    result = invoke(tmp_path, '--domain', 'personal', '--body', '-q', 'needle', '--json')
    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload['status'] == 'incomplete' and payload['scan_complete'] is False
