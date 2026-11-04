"""Exercise real curses in a pseudo-terminal without touching NVML."""
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import pytest
from gpumonitor.storage import Storage


@pytest.mark.parametrize('columns,rows', [(80, 24), (120, 40)])
def test_curses_keys_resize_and_quit(config, rig, tmp_path, columns, rows):
    _, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'database': config.database}))
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
    env = dict(os.environ, TERM='xterm-256color')
    child = subprocess.Popen([sys.executable, '-m', 'gpumonitor', '--config', str(path)],
        stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    output = bytearray()
    try:
        deadline = time.monotonic() + 5
        sent = False
        while time.monotonic() < deadline and child.poll() is None:
            ready, _, _ = select.select([master], [], [], .1)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                output.extend(data)
                if b'GPUWhistle' in output and not sent:
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
                    os.write(master, b'hh3gundmis\t \n7r?q')
                    sent = True
        child.wait(timeout=3)
        assert child.returncode == 0, output.decode(errors='replace')
        assert b'GPUWhistle' in output and b'Traceback' not in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)


@pytest.mark.parametrize('terminal,extra', [
    ('xterm-256color', {}), ('xterm', {}), ('screen-256color', {}),
    ('vt100', {}), ('xterm-256color', {'NO_COLOR': ''}),
    ('xterm-256color', {'GPUMONITOR_ASCII': '1'}),
])
def test_terminal_fallbacks_and_repeated_resize(config, rig, tmp_path, terminal, extra):
    _, _, backend, sample = rig
    backend.cards = {'GPU-' + str(i): [10+i] for i in range(8)}
    with Storage(config.database) as db:
        w = sample()
        shift = time.time() - w['end']
        w['start'] += shift
        w['end'] += shift
        db.save(w, config)
    path = tmp_path / 'resize.json'
    path.write_text(json.dumps({'database': config.database}))
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
    env = dict(os.environ, TERM=terminal)
    env.pop('NO_COLOR', None)
    env.pop('GPUMONITOR_ASCII', None)
    env.update(extra)
    child = subprocess.Popen([sys.executable, '-m', 'gpumonitor', '--config', str(path)],
        stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    output, index, stats_entered = bytearray(), 0, False
    sizes = [(100,30), (120,35), (160,45), (40,12), (80,24)]
    next_change, deadline = time.monotonic()+.2, time.monotonic()+5
    try:
        while child.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], .03)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            if b'GPUWhistle' in output and not stats_entered:
                os.write(master, b't')
                stats_entered = True
            if b'IDLE OCCUPANCY' in output and time.monotonic() >= next_change:
                if index < len(sizes):
                    columns, rows = sizes[index]
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
                    os.kill(child.pid, signal.SIGWINCH)
                    index += 1
                    next_change = time.monotonic()+.12
                else:
                    os.write(master, b'\n\n703gatt7rhhq')
                    next_change = deadline+1
        child.wait(timeout=3)
        assert child.returncode == 0 and index == len(sizes), output.decode(errors='replace')
        assert b'Traceback' not in output and b'CURRENT' in output and b'HISTORY' in output
        assert b'IDLE OCCUPANCY' in output and b'ZERO~' in output and b'HELD~' in output
        if 'GPUMONITOR_ASCII' in extra:
            assert all(byte < 128 for byte in output)
        if 'NO_COLOR' in extra or terminal == 'vt100':
            assert b'\x1b[31m' not in output and b'\x1b[32m' not in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
