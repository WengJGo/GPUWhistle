#!/usr/bin/env python3
"""Audit release trees and Git objects without printing matched private values.

This is a focused publication check, not a substitute for manual review or a
full secret scanner. Worktree exclusions are reported and must not be published.
"""
import argparse
import os
import re
import socket
import struct
import subprocess
from pathlib import Path, PurePosixPath


EXPECTED_EMAIL = '150679271+WengJGo@users.noreply.github.com'

EXCLUDED_DIRS = {
    '.git', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'build', 'dist', 'wheelhouse',
    '.ssh', '.idea', '.vscode', 'data', 'logs', 'exports', 'htmlcov',
}
EXCLUDED_FILES = {'DEPLOYMENT.md', 'VALIDATION.md', '.git-credentials', '.netrc',
                  'config.json', 'theme.json', '.coverage'}
PATTERNS = (
    ('private-key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ('github-token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b')),
    ('cloud-key', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('credential-url', re.compile(r'https?://[^\s/:@]+:[^\s/@]+@')),
    ('credential-assignment', re.compile(
        r'(?i)\b(?:api[_-]?key|access[_-]?token|password|secret[_-]?key)\s*[=:]\s*[\"\']?[A-Za-z0-9_+/=-]{16,}')),
    ('private-path', re.compile(r'/(?:home|data)/[A-Za-z0-9_.-]+(?:/[^\s\"\']*)?')),
    ('server-identifier', re.compile(r'\b(?:server\d+|\d+kd\d+)\b', re.I)),
    ('ipv4-address', re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')),
    ('email-address', re.compile(r'\b[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
)


def excluded(path):
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ''
    return (any(p in EXCLUDED_DIRS or p.endswith('.egg-info') for p in parts)
            or name in EXCLUDED_FILES or name.startswith(('.env', '.coverage.'))
            or name.endswith(('.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.log',
                              '.pid', '.csv', '.ansi', '.whl', '.tar.gz', '.zip',
                              '.pem', '.key', '.p12', '.pfx', '.bak', '.tmp'))
            or any(suffix in name for suffix in ('.db-', '.sqlite-', '.sqlite3-'))
            or (name.endswith('.json') and name not in {'config.example.json', 'theme.example.json'})
            or ('tui-snapshots' in parts and name.endswith('.txt')))


def inspect_content(path, content):
    """Return only (category, path, line); never retain matched values."""
    findings = []
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        offset = 8
        while offset + 12 <= len(content):
            length = struct.unpack('>I', content[offset:offset + 4])[0]
            chunk = content[offset + 4:offset + 8]
            if chunk in {b'tEXt', b'zTXt', b'iTXt', b'eXIf'}:
                findings.append(('image-metadata-review', path, 0))
            offset += 12 + length
        return findings
    if b'\x00' in content:
        return [('unexpected-binary', path, 0)]
    text = content.decode('utf-8', errors='replace')
    local_values = {socket.gethostname(), os.environ.get('HOSTNAME', '')}
    home_name = Path.home().name
    if len(home_name) >= 3:
        local_values.add(home_name)
    for line_no, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group()
                if category == 'email-address' and (value.endswith('@users.noreply.github.com')
                        or value.endswith('@example.com') or value.endswith('@example.org')):
                    continue
                if category == 'ipv4-address':
                    octets = [int(part) for part in value.split('.')]
                    if any(part > 255 for part in octets):
                        continue
                findings.append((category, path, line_no))
                break
        if any(re.search(r'(?<![\w])' + re.escape(value) + r'(?![\w])', line)
               for value in local_values if len(value) >= 3):
            findings.append(('local-identity', path, line_no))
    return findings


def git(*args):
    return subprocess.check_output(['git', *args])


def git_tree(ref=None):
    if ref is None:
        records = git('ls-files', '--stage', '-z').split(b'\0')
    else:
        records = git('ls-tree', '-r', '-z', ref).split(b'\0')
    for record in records:
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, middle, last = metadata.split()
        oid = middle if ref is None else last
        path = raw_path.decode('utf-8', errors='replace')
        yield mode, oid.decode(), path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--staged', action='store_true')
    modes.add_argument('--history', metavar='REF')
    modes.add_argument('--worktree', type=Path, metavar='DIRECTORY')
    args = parser.parse_args()
    findings, count, skipped = [], 0, 0
    if args.worktree:
        for directory, dirs, files in os.walk(args.worktree):
            kept = []
            for name in dirs:
                relative = (Path(directory) / name).relative_to(args.worktree).as_posix()
                if excluded(relative):
                    skipped += 1
                else:
                    kept.append(name)
            dirs[:] = kept
            for name in files:
                item = Path(directory) / name
                relative = item.relative_to(args.worktree).as_posix()
                if excluded(relative):
                    skipped += 1
                    continue
                if item.is_symlink():
                    findings.append(('symlink-review', relative, 0))
                else:
                    findings.extend(inspect_content(relative, item.read_bytes()))
                count += 1
    else:
        refs = git('rev-list', args.history).decode().splitlines() if args.history else [None]
        expected_email = EXPECTED_EMAIL
        seen = set()
        for ref in refs:
            if ref:
                author, author_email, committer, committer_email, message = git(
                    'show', '-s', '--format=%an%n%ae%n%cn%n%ce%n%B', ref).decode().split('\n', 4)
                if (author != 'WenJGo' or committer != 'WenJGo'
                        or author_email != expected_email or committer_email != expected_email
                        or not expected_email.endswith('@users.noreply.github.com')):
                    findings.append(('commit-identity', ref, 0))
                if re.search(r'(?i)\b(?:codex|openai)\b|co-authored-by:.*\bAI\b', message):
                    findings.append(('commit-attribution', ref, 0))
                findings.extend(inspect_content('commit:' + ref, message.encode()))
            for mode, oid, path in git_tree(ref):
                if (oid, path) in seen:
                    continue
                seen.add((oid, path))
                if excluded(path):
                    findings.append(('excluded-artifact', path, 0))
                if mode not in {b'100644', b'100755'}:
                    findings.append(('file-mode-review', path, 0))
                findings.extend(inspect_content(path, git('cat-file', 'blob', oid)))
                count += 1
    for category, path, line in sorted(set(findings)):
        print('%s: %s:%d' % (category, path, line))
    print('Audited %d files/objects; excluded %d local-only entries; findings %d.' %
          (count, skipped, len(set(findings))))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
