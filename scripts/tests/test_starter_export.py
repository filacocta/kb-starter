import importlib.util
from pathlib import Path
import subprocess
import sys


def load(name):
    path = Path(__file__).resolve().parents[1] / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_does_not_admit_private_state():
    mod = load('plan-starter-export.py')
    for name in ['_todo.md', '_log.md', '_seen-urls.json', '_tools/runs/run.md',
                 '_tools/reviews/hash.json', '_tools/staging/source.md',
                 '_tools/deploy/kb-auto-runbook.json', '_tools/kb-agent-policy.py',
                 '_tools/kb-auto-codex.py', '_tools/kb-auto-state.py',
                 'topics/example/sources/private.md', 'readinglist/Links.md']:
        assert not mod.allowed(name), name
    assert mod.allowed('_rules.md')
    assert mod.allowed('_tools/README.md')
    assert mod.allowed('_tools/tests/conftest.py')
    assert mod.allowed('_tools/requirements-dev.txt')
    assert mod.allowed('_tools/kb-write-guard.py')
    assert mod.allowed('_tools/tests/test_kb_seen.py')
    assert not mod.allowed('_tools/kb-unreviewed-new-helper.py')


def test_export_rejects_symlink_components(tmp_path):
    mod = load('plan-starter-export.py')
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'helper.py').write_text('private')
    root = tmp_path / 'root'
    root.mkdir()
    (root / '_tools').symlink_to(private, target_is_directory=True)
    assert not mod.regular_under(root, '_tools/helper.py')


def test_export_rejects_symlink_root(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    script = Path(__file__).resolve().parents[1] / 'plan-starter-export.py'
    result = subprocess.run([sys.executable, str(script), '--kb-root', str(link)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'must not be symlinks' in result.stderr


def test_privacy_reports_categories_without_values():
    mod = load('check-starter-privacy.py')
    value = 'person' + '@' + 'private.invalid'
    found = mod.findings('README.md', value.encode())
    assert found == [('README.md', 1, 'private-email')]
    assert value not in str(found)
    assert mod.findings('_tools/evidence/x.json', b'{}')
    assert mod.findings('knowledgebase/_media/images/private.png', b'\x89PNG')
    assert mod.findings('knowledgebase/topics/private/sources/note.md', b'content')
    assert not mod.findings('knowledgebase/topics/obsidian/sources/public.md', b'public seed')
    assert mod.findings('file.md', b'custom-private-name', ['custom-private-name'])
    assert not mod.findings('file.md', b'help@example.com /home/user/vault')
