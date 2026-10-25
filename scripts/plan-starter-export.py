#!/usr/bin/env python3
"""Compare explicit starter scaffolding with a live KB; never copy file content."""
import argparse
import hashlib
from pathlib import Path
import subprocess


MANIFEST = Path(__file__).with_name('starter-export-paths.txt')
REVIEWED_PATHS = frozenset(line for line in MANIFEST.read_text().splitlines()
                          if line and not line.startswith('#'))


def allowed(path):
    if path not in REVIEWED_PATHS:
        return False
    parts = Path(path).parts
    if len(parts) == 1 and path in {'AGENTS.md', '_contract.json', '_schema.md', '_rules.md', '_schema-media.md'}:
        return True
    if len(parts) == 2 and parts[0] == '_workflows' and parts[1].endswith('.md'):
        return True
    if len(parts) == 2 and parts[0] == '_templates' and parts[1].endswith('.md'):
        return True
    if path in {'_tools/README.md', '_tools/requirements-dev.txt'}:
        return True
    if len(parts) == 2 and parts[0] == '_tools':
        return parts[1].endswith('.py') and parts[1] not in {
            'kb-agent-policy.py', 'kb-auto-codex.py', 'kb-auto-state.py', 'kb-work-validate.py'}
    return len(parts) == 3 and parts[:2] == ('_tools', 'tests') and (parts[2].startswith('test_') or parts[2] == 'conftest.py') and parts[2].endswith('.py')


def regular_under(root, relative):
    path = root / relative
    if path.is_symlink() or not path.is_file():
        return False
    return path.resolve() == root.resolve() / relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kb-root', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true', help='compatibility flag; all runs are read-only')
    args = parser.parse_args()
    source = args.kb_root.expanduser().absolute()
    if source.resolve() != source or source.is_symlink():
        parser.error('KB root and its components must not be symlinks; use the canonical path')
    if not source.is_dir():
        parser.error('KB root must be an existing directory')
    repo = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z', 'knowledgebase/']).decode().split('\0')
    print('READ-ONLY plan: no copies, deletions or credential reads. Review each candidate before porting.')
    for name in sorted(filter(None, tracked)):
        relative = name.removeprefix('knowledgebase/')
        if not allowed(relative):
            continue
        if not regular_under(source, relative) or not regular_under(repo / 'knowledgebase', relative):
            print('unavailable-or-symlink:', relative)
            continue
        before = (repo / name).read_bytes()
        after = (source / relative).read_bytes()
        if hashlib.sha256(before).digest() != hashlib.sha256(after).digest():
            print('review-difference:', relative)
    print('New helpers require explicit dependency/privacy review; this list covers only tracked starter paths.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
