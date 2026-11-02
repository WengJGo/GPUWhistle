from types import SimpleNamespace as S
import pytest
from gpumonitor import scheduler as s


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(s, 'systemd_available', lambda: False)
    monkeypatch.setattr(s.shutil, 'which', lambda name: '/usr/bin/' + name)
    state = {'cron': '0 1 * * * echo unrelated\n', 'calls': []}
    def run(args, **kwargs):
        state['calls'].append(args)
        if args == ['crontab', '-l']:
            return S(returncode=0, stdout=state['cron'], stderr='')
        if args == ['crontab', '-']:
            state['cron'] = kwargs['input']
        return S(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(s, 'run', run)
    return state


def test_cron_idempotent_and_preserves_other_jobs(config, runner):
    s.install(config, backend='cron')
    s.install(config, backend='cron')
    assert runner['cron'].count(s.BEGIN) == 1
    assert 'echo unrelated' in runner['cron']
    assert '--scheduled' in runner['cron']
    s.remove()
    s.remove()
    assert runner['cron'] == '0 1 * * * echo unrelated\n'


def test_systemd_idempotent_and_owned(config, runner, monkeypatch):
    monkeypatch.setattr(s, 'systemd_available', lambda: True)
    s.install(config, backend='systemd')
    s.install(config, backend='systemd')
    timer = s.unit_dir() / 'gpumonitor.timer'
    assert timer.read_text().count('OnUnitActiveSec=600s') == 1
    s.remove()
    assert not timer.exists()
    timer.write_text('unrelated service')
    with pytest.raises(RuntimeError, match='unmanaged'):
        s.install(config, backend='systemd')
    s.remove()
    assert timer.read_text() == 'unrelated service'


def test_cron_malformed_not_modified(config, runner):
    runner['cron'] += s.BEGIN + '\n'
    with pytest.raises(RuntimeError, match='malformed'):
        s.install(config, backend='cron')
    assert runner['cron'].endswith(s.BEGIN + '\n')


def test_cron_percent_escaped(config, runner):
    s.install(config, '/tmp/example%name.json', backend='cron')
    assert '\\%' in runner['cron']


def test_generated_units_validate(config, runner, monkeypatch):
    import subprocess
    # runner replaces shutil.which; use the executable only when really present.
    executable = '/usr/bin/systemd-analyze'
    from pathlib import Path
    if not Path(executable).exists():
        pytest.skip('systemd-analyze unavailable')
    monkeypatch.setattr(s, 'systemd_available', lambda: True)
    s.install(config, backend='systemd')
    paths = [str(s.unit_dir() / name) for name in ('gpumonitor.service', 'gpumonitor.timer')]
    result = subprocess.run([executable, '--user', 'verify'] + paths, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
