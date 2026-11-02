import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = '# Managed by GPUmonitor v1'
BEGIN = '# BEGIN GPUmonitor v1'
END = '# END GPUmonitor v1'


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=15, env=dict(os.environ, LC_ALL='C'), **kwargs)


def unit_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'


def systemd_available():
    if not shutil.which('systemctl'):
        return False
    try:
        return run(['systemctl', '--user', 'show-environment']).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def command(config_file=None):
    args = [sys.executable, '-m', 'gpumonitor']
    if config_file:
        args += ['--config', str(Path(config_file).resolve())]
    if any(any(ord(c) < 32 for c in arg) for arg in args):
        raise ValueError('scheduled command paths must not contain control characters')
    return args


def systemd_quote(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def cron_content():
    result = run(['crontab', '-l'])
    if result.returncode:
        if 'no crontab' in result.stderr.lower():
            return ''
        raise RuntimeError('cannot read crontab: ' + result.stderr.strip())
    return result.stdout


def strip_block(content):
    if BEGIN not in content and END not in content:
        return content
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError('malformed GPUmonitor cron block; leaving crontab untouched')
    return ''.join(lines[:starts[0]] + lines[ends[0] + 1:])


def owned(path):
    return path.exists() and not path.is_symlink() and path.read_text().startswith(MARKER + '\n')


def install(config, config_file=None, backend='auto'):
    use_systemd = systemd_available() if backend == 'auto' else backend == 'systemd'
    args = command(config_file)
    if use_systemd:
        if not systemd_available():
            raise RuntimeError('systemd user manager unavailable; use --backend cron')
        directory = unit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        service, timer = directory / 'gpumonitor.service', directory / 'gpumonitor.timer'
        for path in (service, timer):
            if path.exists() or path.is_symlink():
                if not owned(path):
                    raise RuntimeError('refusing to replace unmanaged file: ' + str(path))
        service.write_text(MARKER + '\n[Unit]\nDescription=GPUWhistle read-only sampling\n[Service]\nType=oneshot\n'
            + 'ExecStart=' + ' '.join(systemd_quote(a) for a in args + ['once']) + '\n'
            + 'UMask=0077\nNice=10\nTimeoutStartSec=' + str(int(config.duration + 120)) + '\n')
        timer.write_text(MARKER + '\n[Unit]\nDescription=GPUWhistle sampling timer\n[Timer]\nOnActiveSec=60\n'
            + 'OnUnitActiveSec=%ss\nAccuracySec=1s\nUnit=gpumonitor.service\n[Install]\nWantedBy=timers.target\n' % config.period)
        for cmd in (['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'enable', '--now', 'gpumonitor.timer'],
                    ['systemctl', '--user', 'restart', 'gpumonitor.timer']):
            result = run(cmd)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or 'systemd installation failed')
        # Avoid two managed schedulers after switching backends.
        if shutil.which('crontab'):
            content = cron_content()
            if BEGIN in content:
                result = run(['crontab', '-'], input=strip_block(content))
                if result.returncode:
                    raise RuntimeError(result.stderr)
        return 'systemd user timer installed. Logout may stop it unless linger is already enabled; ask your administrator if needed.'
    if not shutil.which('crontab'):
        raise RuntimeError('no usable systemd user manager or crontab; install cron or run once manually')
    if config.period < 60 or config.period % 60:
        raise ValueError('cron backend requires period to be a multiple of 60 seconds')
    content = strip_block(cron_content())
    # cron interprets percent signs even inside shell quotes.
    cmd = shlex.join(args + ['once', '--scheduled']).replace('%', '\\%')
    source = shlex.quote(str(Path(__file__).resolve().parent.parent)).replace('%', '\\%')
    block = BEGIN + '\n* * * * * cd ' + source + ' && ' + cmd + '\n' + END + '\n'
    result = run(['crontab', '-'], input=content + ('' if not content or content.endswith('\n') else '\n') + block)
    if result.returncode:
        raise RuntimeError('cron install failed: ' + result.stderr)
    remove_systemd()
    return 'user crontab installed (minute gate; collection exits after each window).'


def remove_systemd():
    directory = unit_dir()
    timer = directory / 'gpumonitor.timer'
    if owned(timer) and systemd_available():
        result = run(['systemctl', '--user', 'disable', '--now', 'gpumonitor.timer'])
        if result.returncode:
            raise RuntimeError(result.stderr)
    changed = False
    for name in ('gpumonitor.service', 'gpumonitor.timer'):
        path = directory / name
        if owned(path):
            path.unlink()
            changed = True
    if changed and systemd_available():
        run(['systemctl', '--user', 'daemon-reload'])


def remove():
    remove_systemd()
    if shutil.which('crontab'):
        content = cron_content()
        if BEGIN in content:
            result = run(['crontab', '-'], input=strip_block(content))
            if result.returncode:
                raise RuntimeError(result.stderr)
    return 'GPUWhistle managed schedule removed; data preserved.'


def status():
    lines = []
    directory = unit_dir()
    lines.append('systemd managed timer: ' + str(owned(directory / 'gpumonitor.timer')))
    if systemd_available():
        result = run(['systemctl', '--user', 'status', 'gpumonitor.timer', '--no-pager'])
        lines.append(result.stdout.strip() or result.stderr.strip())
    if shutil.which('crontab'):
        try:
            lines.append('cron managed entry: ' + str(BEGIN in cron_content()))
        except RuntimeError as exc:
            lines.append(str(exc))
    return '\n'.join(lines)
