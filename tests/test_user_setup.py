import json
import subprocess
from pathlib import Path
import pytest
from gpumonitor.user_setup import configure, initialize_config, markers


def test_path_setup_idempotent_and_removes_only_own_block(tmp_path):
    prefix = tmp_path / 'odd [x] prefix'
    profile = tmp_path / '.bashrc'
    profile.write_text('# user settings\nexport KEEP_ME=yes\n')
    configure(prefix, home=tmp_path, shell='/bin/bash')
    configure(prefix, home=tmp_path, shell='/bin/bash')
    assert profile.read_text().count(markers(prefix)[0]) == 1
    command = 'PATH=/usr/bin; . "$1"; command -v bash; printf "%s" "$PATH"'
    result = subprocess.run(['bash', '-c', command, 'test', str(profile)], capture_output=True, text=True)
    assert result.returncode == 0 and str(prefix / 'bin') in result.stdout
    configure(prefix, remove=True, home=tmp_path)
    assert profile.read_text() == '# user settings\nexport KEEP_ME=yes\n'


def test_setup_preserves_existing_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert initialize_config()
    path = tmp_path / 'gpumonitor/config.json'
    assert json.loads(path.read_text())['period'] == 600
    path.write_text('{"period": 1200}')
    assert not initialize_config() and path.read_text() == '{"period": 1200}'


def test_profile_symlink_not_followed(tmp_path):
    target = tmp_path / 'unrelated'
    target.write_text('keep')
    (tmp_path / '.bashrc').symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        configure(tmp_path / 'prefix', home=tmp_path, shell='/bin/bash')
    assert target.read_text() == 'keep' and not (tmp_path / '.profile').exists()


def test_zsh_profiles_and_existing_login_profile(tmp_path):
    configure(tmp_path / 'prefix', home=tmp_path, shell='/bin/zsh')
    assert (tmp_path / '.zshrc').exists() and (tmp_path / '.zprofile').exists()
    (tmp_path / '.bash_profile').write_text('# login\n')
    configure(tmp_path / 'prefix', home=tmp_path, shell='/bin/bash')
    assert markers(tmp_path / 'prefix')[0] in (tmp_path / '.bash_profile').read_text()
