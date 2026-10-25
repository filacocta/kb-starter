#!/usr/bin/env python3
"""Scan tracked/nonignored starter files; report categories, never secret values.

This catches common export mistakes, not every possible personal fact. Review
the outgoing diff and history as well. Extra private identifiers may be passed
with --deny-token without storing them in the public repository.
"""
import argparse
from pathlib import Path
import re
import subprocess


FORBIDDEN_PARTS = {'auto-state', 'evidence', 'reviews', 'successors', 'staging', 'runs', '.obsidian', '.venv', 'node_modules'}
PATTERNS = {
    'private-home-path': re.compile(r'/(?:home|Users)/(?!user(?:/|\b)|person(?:/|\b)|example(?:/|\b))[^\s/<>"\'`]+/'),
    'private-email': re.compile(r'(?<![\w:/])[\w.+-]+@(?!(?:example\.(?:com|org|net|invalid|test))\b)[\w.-]+\.[A-Za-z]{2,}\b'),
    'credential': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,})\b'),
}


def findings(path, data, deny_tokens=()):
    relative = Path(path)
    parts = relative.parts
    if len(parts) > 2 and parts[:2] == ('knowledgebase', 'topics') and parts[2] != 'obsidian':
        return [(path, 0, 'non-seed-topic')]
    if len(parts) > 2 and parts[:2] == ('knowledgebase', 'projects') and parts[2] != '.gitkeep':
        return [(path, 0, 'non-scaffold-project')]
    if len(parts) > 2 and parts[:2] == ('knowledgebase', '_media') and relative.name != '_README.md':
        return [(path, 0, 'raw-media')]
    if set(relative.parts) & FORBIDDEN_PARTS:
        return [(path, 0, 'private-runtime-path')]
    if relative.name in {'_url-aliases.json', 'kb-agent-policy.py', 'kb-auto-runbook.json'}:
        return [(path, 0, 'local-policy-or-history')]
    if relative.suffix.lower() in {'.pdf', '.epub', '.mp4', '.webm', '.sqlite', '.db'}:
        return [(path, 0, 'raw-payload')]
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return []  # Binary assets need separate human inspection.
    output = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if pattern.search(line):
                output.append((path, line_no, category))
        if any(token.casefold() in line.casefold() for token in deny_tokens):
            output.append((path, line_no, 'private-identifier'))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--deny-token', action='append', default=[])
    args = parser.parse_args()
    if any(not token.strip() for token in args.deny_token):
        parser.error('deny tokens must be nonempty')
    root = args.repo.resolve()
    paths = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode().split('\0')
    output = []
    for path in filter(None, paths):
        target = root / path
        if target.is_symlink() or target.resolve() != root / path:
            output.append((path, 0, 'symlink'))
        elif not target.is_file():
            output.append((path, 0, 'missing-tracked-file'))
        else:
            output.extend(findings(path, target.read_bytes(), args.deny_token))
    for path, line, category in output:
        print(f'{path}:{line}: {category}')
    print(f'{len(output)} findings; tracked/nonignored-file scan, not a privacy certification')
    return bool(output)


if __name__ == '__main__':
    raise SystemExit(main())
