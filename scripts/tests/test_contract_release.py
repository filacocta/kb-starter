import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]


def check(root):
    return subprocess.run([sys.executable, str(REPO / 'scripts/kb-audit'), '--domain', 'personal',
                           '--kb-root', str(root), '--contracts', '--json'], capture_output=True, text=True)


def manifest(root, text):
    specification = json.loads((REPO / 'scripts/kb_contract_spec.json').read_text())
    record = {'author': 'kb-bot', 'version': 1, 'release': specification['release'], 'files': {}}
    for relative in specification['paths']:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = text if relative == '_rules.md' else '# Contract fixture\n'
        path.write_text(content)
        record['files'][relative] = hashlib.sha256(content.encode()).hexdigest()
    (root / '_contract.json').write_text(json.dumps(record))


def test_contract_drift_and_bad_links_are_visible(tmp_path):
    manifest(tmp_path, '# Contract\nKeep evidence grounded.\n')
    assert check(tmp_path).returncode == 0
    (tmp_path / '_rules.md').write_text('# Different\n')
    assert check(tmp_path).returncode == 1
    manifest(tmp_path, '# Contract\n[Missing](absent.md)\n')
    result = check(tmp_path)
    assert result.returncode == 1 and 'missing/outside contract link' in result.stdout


def test_retired_rulebook_cannot_silently_become_authoritative(tmp_path):
    manifest(tmp_path, '# Contract\nRead _rules-synthesis.md and apply Rule 22.\n')
    result = check(tmp_path)
    assert result.returncode == 1 and 'retired policy reference' in result.stdout
    manifest(tmp_path, '# Contract\nKeep evidence grounded.\n')
    (tmp_path / '_rules-media.md').write_text('Old competing behavior.')
    assert check(tmp_path).returncode == 1


def test_checked_in_release_matches_manifest_and_all_flow_links():
    result = check(REPO / 'knowledgebase')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['words'] < 6000


def test_required_contract_cannot_be_omitted_from_verification(tmp_path):
    manifest(tmp_path, '# Contract\nKeep evidence grounded.\n')
    path = tmp_path / '_contract.json'
    data = json.loads(path.read_text())
    data['files'].pop('_tools/README.md')
    path.write_text(json.dumps(data))
    result = check(tmp_path)
    assert result.returncode != 0 and 'complete required file set' in result.stderr
    manifest(tmp_path, '# Contract\nKeep evidence grounded.\n')
    data = json.loads(path.read_text())
    data['release'] = 'unknown'
    path.write_text(json.dumps(data))
    assert check(tmp_path).returncode != 0
