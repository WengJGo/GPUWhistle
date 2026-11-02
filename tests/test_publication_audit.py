"""Publication checks use generated examples, never production credentials."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'audit_publication.py'
spec = importlib.util.spec_from_file_location('publication_audit', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize('path', ['history.db-wal', '.venv/lib/module.py',
                                  'DEPLOYMENT.md', 'config.json'])
def test_runtime_and_private_artifacts_are_rejected(path):
    assert audit.excluded(path)
    assert not audit.excluded('config.example.json')


def test_sensitive_values_are_redacted():
    token = 'gh' + 'p_' + 'A' * 36
    content = ('credential = ' + token + '\n' + '/ho' + 'me/demo/private\n').encode()
    findings = audit.inspect_content('example.py', content)
    assert {finding[0] for finding in findings} >= {'github-token', 'private-path'}
    assert token not in repr(findings)


def test_staged_and_deleted_historical_blobs_are_checked(tmp_path):
    env = dict(os.environ, GIT_AUTHOR_NAME='WenJGo', GIT_COMMITTER_NAME='WenJGo',
               GIT_AUTHOR_EMAIL=audit.EXPECTED_EMAIL,
               GIT_COMMITTER_EMAIL=audit.EXPECTED_EMAIL)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, env=env,
                                       stderr=subprocess.DEVNULL)
    git('init', '-b', 'main')
    git('config', '--local', 'user.name', 'WenJGo')
    git('config', '--local', 'user.email', env['GIT_AUTHOR_EMAIL'])
    token = 'gh' + 'p_' + 'B' * 36
    path = tmp_path / 'example.py'
    path.write_text(token)
    git('add', 'example.py')
    # Scan the Git blob, even when the current worktree content is clean.
    path.write_text('safe\n')
    result = subprocess.run([os.sys.executable, str(SCRIPT), '--staged'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1 and 'github-token' in result.stdout
    assert token not in result.stdout + result.stderr
    git('commit', '-m', 'Example fixture')
    git('add', 'example.py')
    git('commit', '-m', 'Remove fixture value')
    result = subprocess.run([os.sys.executable, str(SCRIPT), '--history', 'main'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1 and 'github-token' in result.stdout
    assert token not in result.stdout + result.stderr
