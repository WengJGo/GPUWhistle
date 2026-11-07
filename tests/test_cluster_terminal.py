"""Exercise live view, fallback colors, resize and cancellation in real curses."""
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
from test_cluster import card


def start(config, rig, tmp_path, monkeypatch, slow=False, extra=None):
    _, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'database':config.database}))
    cluster = tmp_path/'cluster.json'
    cluster.write_text(json.dumps(dict(nodes=[dict(name='node-'+str(n),target='node-'+str(n)) for n in range(5)], timeout=60)))
    bindir = tmp_path/'bin'
    bindir.mkdir()
    pidfile = tmp_path/'probe-pids'
    fake = bindir/'ssh'
    if slow:
        body = 'import os,time\nwith open(%r,"a") as f:f.write(str(os.getpid())+"\\n")\ntime.sleep(60)\n' % str(pidfile)
    else:
        body = 'print(%r)\n' % json.dumps({'gpus':[card(), card(index=1, used_mib=None, gpu_util=None, process_count=None)]})
    fake.write_text('#!'+sys.executable+'\n'+body)
    fake.chmod(0o755)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',24,80,0,0))
    env = dict(os.environ, TERM='xterm-256color', GPUMONITOR_CLUSTER=str(cluster), PATH=str(bindir)+os.pathsep+os.environ['PATH'])
    env.pop('NO_COLOR', None)
    env.pop('GPUMONITOR_ASCII', None)
    env.update(extra or {})
    proc = subprocess.Popen([sys.executable, '-m', 'gpumonitor', '--config', str(path)], stdin=slave,stdout=slave,stderr=slave,env=env)
    os.close(slave)
    return proc, master, pidfile


def read(master, output):
    if select.select([master], [], [], .03)[0]:
        try:
            output.extend(os.read(master,65536))
        except OSError:
            pass


@pytest.mark.parametrize('extra', [{}, {'NO_COLOR':''}, {'GPUMONITOR_ASCII':'1'}, {'TERM':'vt100'}])
def test_live_view_colors_resize_back_and_rescan(config, rig, tmp_path, monkeypatch, extra):
    proc, master, _ = start(config,rig,tmp_path,monkeypatch,extra=extra)
    output = bytearray()
    phase, size = 0, 0
    sizes = [(160,45), (100,30), (40,12), (80,24)]
    deadline, change = time.monotonic()+8, 0
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            read(master, output)
            if phase == 0 and b'GPUWhistle' in output:
                os.write(master,b'c');phase=1
            elif phase == 1 and b'GPU DETAILS' in output and b'UNKNOWN' in output:
                phase=2
            elif phase == 2 and time.monotonic() >= change:
                if size < len(sizes):
                    columns, rows = sizes[size]
                    fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,columns,0,0))
                    os.kill(proc.pid, signal.SIGWINCH)
                    size += 1;change=time.monotonic()+.12
                else:
                    os.write(master,b'hhbccttq');phase=3
        proc.wait(timeout=2)
        assert proc.returncode == 0 and phase == 3, output.decode(errors='replace')
        assert b'CLUSTER LIVE' in output and b'Traceback' not in output and b'N/A' in output
        if 'NO_COLOR' in extra or extra.get('TERM') == 'vt100':
            assert b'\x1b[32m' not in output
        elif 'GPUMONITOR_ASCII' not in extra:
            assert b'\x1b[32m' in output
        if 'GPUMONITOR_ASCII' in extra:
            assert all(b < 128 for b in output)
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
        os.close(master)


def test_q_exits_while_ssh_pending_and_reaps_probe_children(config,rig,tmp_path,monkeypatch):
    proc,master,pidfile = start(config,rig,tmp_path,monkeypatch,slow=True)
    output, sent, quit_at = bytearray(), False, None
    deadline=time.monotonic()+5
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            read(master,output)
            if b'GPUWhistle' in output and not sent:
                os.write(master,b'c');sent=True
            if b'CLUSTER LIVE' in output and pidfile.exists() and quit_at is None:
                quit_at=time.monotonic();os.write(master,b'q')
        proc.wait(timeout=2)
        assert proc.returncode == 0 and quit_at is not None
        assert time.monotonic()-quit_at < 2
        for pid in pidfile.read_text().splitlines():
            path = '/proc/'+pid+'/stat'
            assert not os.path.exists(path) or open(path).read().split()[2] == 'Z'
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
        os.close(master)
        if pidfile.exists():
            for pid in pidfile.read_text().splitlines():
                try:
                    os.killpg(int(pid),signal.SIGKILL)
                except ProcessLookupError:
                    pass
