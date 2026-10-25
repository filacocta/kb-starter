import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

REPO = Path(__file__).resolve().parents[2]


def install(tmp_path, *extra):
    return subprocess.run([sys.executable, str(REPO / 'skills/install.py'), '--agent', 'custom',
                           '--destination', str(tmp_path / 'skills'), '--config', str(tmp_path / 'roots.md'),
                           *extra], capture_output=True, text=True)


def test_install_status_dry_run_and_modified_files(tmp_path):
    result = install(tmp_path, '--dry-run')
    assert result.returncode == 0 and not (tmp_path / 'skills').exists()
    assert install(tmp_path).returncode == 0
    assert install(tmp_path, '--status').returncode == 0
    skill = tmp_path / 'skills/kb-query/SKILL.md'
    skill.write_text(skill.read_text() + '\nHuman customization.\n')
    assert install(tmp_path, '--status').returncode == 1
    assert install(tmp_path).returncode == 2
    assert 'Human customization.' in skill.read_text()


def test_manifest_cleanup_preserves_modified_stale_files(tmp_path):
    assert install(tmp_path).returncode == 0
    stale = tmp_path / 'skills/kb-query/old-reference.md'
    original = 'previously managed data\n'
    stale.write_text(original)
    path = tmp_path / 'skills/.kb-release.json'
    manifest = json.loads(path.read_text())
    manifest['files'][str(stale)] = hashlib.sha256(original.encode()).hexdigest()
    path.write_text(json.dumps(manifest))
    stale.write_text('preserve a human edit\n')
    assert install(tmp_path, '--force').returncode == 2
    assert stale.read_text() == 'preserve a human edit\n'
    stale.write_text(original)
    assert install(tmp_path, '--force').returncode == 0
    assert not stale.exists()


def test_explicit_index_update_sees_equal_size_backdated_edits(tmp_path):
    source = tmp_path / 'topics/demo/notes/note.md'
    source.parent.mkdir(parents=True)
    source.write_text('---\ntitle: Earlier\ntype: concept\n---\nText.\n')
    command = [sys.executable, str(REPO / 'scripts/kb-index'), '--kb-root', str(tmp_path)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    old_time = source.stat().st_mtime
    source.write_text(source.read_text().replace('Earlier', 'Changed'))
    os.utime(source, (old_time - 1000, old_time - 1000))
    assert subprocess.run([*command, '--update'], capture_output=True).returncode == 0
    record = json.loads((tmp_path / '_kb-index.json').read_text())['records'][0]
    assert record['title'] == 'Changed'


def test_duplicate_audit_reports_without_changing_bytes(tmp_path):
    for relative in ('projects/widget/one.md', 'team-share/two.md'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Legacy\nRetained authored text.\n')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*.md')}
    command = [sys.executable, str(REPO / 'scripts/kb-audit'), '--domain', 'work', '--kb-root', str(tmp_path), '--json']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report['duplicate_groups'] == 1 and report['metadata_debt_files'] == 2
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*.md')} == before


def test_installed_unicode_and_quoted_root_round_trips(tmp_path):
    root = tmp_path / 'café "notes"'
    note = root / 'topics/demo/note.md'
    note.parent.mkdir(parents=True)
    note.write_text('# Needle\nA specific match.\n')
    assert install(tmp_path, '--personal-root', str(root)).returncode == 0
    search = tmp_path / 'kb-tools/kb-search'
    env = dict(os.environ)
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    before = {str(p) for p in (tmp_path / 'kb-tools').rglob('*')}
    result = subprocess.run([sys.executable, str(search), '--config', str(tmp_path / 'roots.md'),
                             '--domain', 'personal', '--body', '-q', 'needle', '--json'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['root'] == str(root)
    assert {str(p) for p in (tmp_path / 'kb-tools').rglob('*')} == before


def test_renamed_document_is_checked_as_changed(tmp_path):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    path = tmp_path / 'projects/widget/note.md'
    path.parent.mkdir(parents=True)
    path.write_text('---\ntitle: Note\ntype: note\nstatus: draft\nmodified: 2026-01-01\n---\n' + 'Long unchanged body.\n' * 100)
    git('add', '.')
    git('commit', '-m', 'fixture')
    renamed = path.with_name('renamed.md')
    path.rename(renamed)
    renamed.write_text(renamed.read_text().replace('2026-01-01', 'invalid'))
    git('add', '.')
    result = subprocess.run([sys.executable, str(REPO / 'scripts/kb-audit'), '--domain', 'work',
                             '--kb-root', str(tmp_path), '--changed-from', 'HEAD', '--json'], capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)['changed_files_with_errors'] == 1


def test_changed_and_untracked_quoted_git_paths_are_checked(tmp_path):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-q')
    path = tmp_path / 'notes/café.md'
    path.parent.mkdir(parents=True)
    valid = '---\ntitle: Note\ntype: note\nstatus: draft\nmodified: 2026-01-01\n---\n# Note\n'
    path.write_text(valid)
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    path.write_text(valid.replace('2026-01-01', 'invalid'))
    new = path.with_name('new-ü\tfile.md')
    new.write_text('# New note without metadata\n')
    template = tmp_path / 'team-share/templates/current-context.md'
    template.parent.mkdir(parents=True)
    template.write_text('# New template without metadata\n')
    result = subprocess.run([sys.executable, str(REPO / 'scripts/kb-audit'), '--domain', 'work',
                             '--kb-root', str(tmp_path), '--changed-from', 'HEAD', '--json'], capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report['changed_files_with_errors'] == 3
    assert {row['path'] for row in report['findings'] if row['changed']} == {
        'notes/café.md', 'notes/new-ü\tfile.md', 'team-share/templates/current-context.md'}


def test_known_legacy_contract_is_removed_but_customizations_stop_upgrade(tmp_path):
    fixture = tmp_path / 'release'
    shutil.copytree(REPO / 'skills', fixture / 'skills')
    shutil.copytree(REPO / 'scripts', fixture / 'scripts', ignore=shutil.ignore_patterns('__pycache__', 'tests'))
    original = '# Exact previous contract\n'
    legacy = {'files': {'kb-ask/SKILL.md': hashlib.sha256(original.encode()).hexdigest()}}
    (fixture / 'skills/legacy-manifest.json').write_text(json.dumps(legacy))
    old = tmp_path / 'installed/kb-ask/SKILL.md'
    old.parent.mkdir(parents=True)
    old.write_text(original + 'Human edit.\n')
    command = [sys.executable, str(fixture / 'skills/install.py'), '--agent', 'custom',
               '--destination', str(tmp_path / 'installed'), '--config', str(tmp_path / 'roots.md')]
    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode == 2 and old.exists()
    assert not (tmp_path / 'installed/kb-query').exists()
    old.write_text(original)
    accepted = subprocess.run(command, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    assert not old.exists() and (tmp_path / 'installed/kb-query/SKILL.md').is_file()
