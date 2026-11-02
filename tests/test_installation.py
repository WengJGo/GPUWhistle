import os
import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['gpumonitor', 'seegpu'])
def test_scripts_refuse_unmanaged_files(tmp_path, name):
    prefix = tmp_path / 'prefix'
    entry = prefix / 'bin' / name
    entry.parent.mkdir(parents=True)
    entry.write_text('unrelated')
    env = dict(os.environ, GPUMONITOR_PREFIX=str(prefix))
    result = subprocess.run(['bash', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and 'Refusing unmanaged' in result.stderr
    result = subprocess.run(['bash', str(ROOT / 'uninstall.sh')], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and entry.read_text() == 'unrelated'
