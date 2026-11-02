"""Run real setup control flow with isolated package/CLI stubs, never real cron."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup_stub(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'setup.sh').write_bytes((ROOT / 'setup.sh').read_bytes())
    (source / 'install.sh').write_text('#!/bin/bash\nexit 0\n')
    prefix = tmp_path / 'prefix'
    cli = prefix / 'bin/seegpu'
    python = prefix / 'share/gpumonitor/venv/bin/python'
    for script in (cli, python):
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('#!/bin/bash\nexit 0\n')
        script.chmod(0o755)
    cli.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$SETUP_TEST_CALLS"
if [[ "$1" == schedule && "${SETUP_TEST_SCHEDULE_FAIL:-0}" == 1 ]]; then
    exit 1
fi
''')
    tools = tmp_path / 'bin'
    tools.mkdir()
    crontab = tools / 'crontab'
    crontab.write_text('#!/bin/bash\nexit 99\n')
    crontab.chmod(0o755)
    log = tmp_path / 'calls'
    env = dict(os.environ, GPUMONITOR_PREFIX=str(prefix), SETUP_TEST_CALLS=str(log),
               PATH=str(tools) + os.pathsep + os.environ['PATH'])
    def run(*args, fail=False):
        result = subprocess.run(['bash', str(source / 'setup.sh'), *args],
                                env=dict(env, SETUP_TEST_SCHEDULE_FAIL=str(int(fail))),
                                capture_output=True, text=True)
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls
    return run


def test_default_setup_enables_cron_and_takes_first_sample(setup_stub):
    result, calls = setup_stub()
    assert result.returncode == 0
    assert calls == ['doctor', 'once', 'schedule install --backend cron']


def test_skip_first_sample_keeps_automatic_collection_enabled(setup_stub):
    result, calls = setup_stub('--no-sample')
    assert result.returncode == 0
    assert calls == ['schedule install --backend cron']


def test_schedule_failure_cannot_report_setup_success(setup_stub):
    result, calls = setup_stub('--no-sample', fail=True)
    assert result.returncode == 1
    assert calls == ['schedule install --backend cron']
    assert '自动采集未启用' in result.stderr
    assert '安装配置完成' not in result.stdout


def test_explicit_opt_out_remains_available(setup_stub):
    result, calls = setup_stub('--no-sample', '--no-schedule')
    assert result.returncode == 0 and calls == []
