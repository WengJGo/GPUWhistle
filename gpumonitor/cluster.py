"""On-demand cluster availability. Probes never write sampling history."""
import json
import math
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def cluster_path():
    return Path(os.environ.get('GPUMONITOR_CLUSTER',
        str(Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'gpumonitor/cluster.json')))


@dataclass(frozen=True)
class ClusterConfig:
    nodes: tuple = ()
    timeout: float = 12
    free_memory_mib: float = 256
    stale_after: float = 30
    refresh_interval: float = 10

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else cluster_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict) or set(data)-{'nodes', 'timeout', 'free_memory_mib', 'stale_after', 'refresh_interval'}:
            raise ValueError('Invalid cluster configuration keys')
        config = cls(**dict(data, nodes=tuple(data.get('nodes', ()))))
        for key, lower, upper in (('timeout', 1, 60), ('free_memory_mib', 0, 4096), ('stale_after', 1, 300), ('refresh_interval', 1, 300)):
            value = getattr(config, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError('Invalid cluster ' + key)
        names = set()
        if len(config.nodes) > 32:
            raise ValueError('At most 32 cluster nodes')
        for node in config.nodes:
            if not isinstance(node, dict) or set(node) != {'name', 'target'}:
                raise ValueError('Each cluster node requires name and target')
            name, target = node['name'], node['target']
            if not isinstance(name, str) or not name or len(name) > 64 or not name.isprintable() or name in names:
                raise ValueError('Invalid or duplicate cluster node name')
            names.add(name)
            if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,254}', target):
                raise ValueError('Cluster target must be an SSH destination or local')
        return config

    @property
    def refresh_seconds(self):
        # Short expiry settings must also refresh before their deadline.
        return min(self.refresh_interval, self.stale_after / 2)


def _number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def classify(gpu, threshold):
    """FREE requires every check; missing or malformed readings never mean zero."""
    used, free, total, util, count = (gpu.get(k) for k in ('used_mib', 'free_mib', 'total_mib', 'gpu_util', 'process_count'))
    valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
    if gpu.get('compute_mode') == 'Prohibited':
        return 'UNAVAILABLE'
    if ((valid_count and count > 0) or (_number(used) and used > threshold)
            or (_number(util) and 0 < util <= 100)):
        return 'IN_USE'
    if (not all(_number(v) for v in (used, free, total, util)) or total <= 0 or used > total
            or free > total or used+free > total+1 or util > 100 or not valid_count
            or gpu.get('mig') is not False or gpu.get('compute_mode') not in ('Default', 'Exclusive_Process', 'Exclusive_Thread')):
        return 'UNKNOWN'
    if free == 0:
        return 'UNAVAILABLE'
    return 'FREE' if count == 0 and used <= threshold and util == 0 else 'UNKNOWN'


def gpu_status(gpu, result, config, now=None):
    now = time.monotonic() if now is None else now
    if now-result.get('sample_monotonic', result['started_monotonic']) > config.stale_after:
        return 'STALE'
    return gpu['status']


class LiveScan:
    """Poll child pipes without threads or blocking curses; close cancels probes."""
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.jobs = {}
        self.results = []
        self.config = ClusterConfig()
        self.next_refresh = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        self.selector.close()

    def start(self, config, keep_results=False):
        previous = {r['name']: r for r in self.results} if keep_results else {}
        self.close()
        self.results = []
        self.config = config
        self.next_refresh = time.monotonic()+config.refresh_seconds if config.nodes else None
        source = Path(__file__).with_name('cluster_probe.py').read_text()
        for node in config.nodes:
            result = dict(name=node['name'], status='SCANNING', gpus=[], error=None,
                          started_monotonic=time.monotonic(), checked_at=None)
            old = previous.get(node['name'])
            if old and old['status'] == 'DONE':
                result.update(status='REFRESHING', gpus=old['gpus'], checked_at=old['checked_at'],
                    sample_monotonic=old.get('sample_monotonic', old['started_monotonic']))
            self.results.append(result)
            command = ([sys.executable, str(Path(__file__).with_name('cluster_probe.py'))] if node['target'] == 'local' else
                ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                 '-o', 'ConnectTimeout=%s' % min(8, math.ceil(config.timeout)),
                 '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=1',
                 node['target'], 'python3 -c ' + shlex.quote(source)])
            try:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True)
            except OSError as exc:
                result.update(status='ERROR', error=str(exc), gpus=[])
                continue
            job = dict(proc=proc, result=result, stdout=bytearray(), stderr=bytearray())
            self.jobs[proc.pid] = job
            for key in ('stdout', 'stderr'):
                pipe = getattr(proc, key)
                os.set_blocking(pipe.fileno(), False)
                self.selector.register(pipe, selectors.EVENT_READ, (job, key))

    def _drain(self):
        for event, _ in self.selector.select(0):
            job, key = event.data
            try:
                data = os.read(event.fileobj.fileno(), 65536)
            except BlockingIOError:
                continue
            if data:
                job[key].extend(data)
            else:
                self.selector.unregister(event.fileobj)

    def _stop(self, job):
        proc = job['proc']
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=1)
        for key in ('stdout', 'stderr'):
            pipe = getattr(proc, key)
            try:
                self.selector.unregister(pipe)
            except KeyError:
                pass
            pipe.close()
        self.jobs.pop(proc.pid, None)

    def poll(self):
        was_running = self.running
        # Drain all ready bytes; even inventories larger than a pipe buffer must finish.
        for _ in range(16):
            if not self.selector.select(0):
                break
            self._drain()
        now = time.monotonic()
        for job in list(self.jobs.values()):
            proc, result = job['proc'], job['result']
            error = None
            if sum(len(job[k]) for k in ('stdout', 'stderr')) > 1024*1024:
                error = 'Probe response too large'
            elif now-result['started_monotonic'] >= self.config.timeout:
                error = 'Timed out after %.0fs' % self.config.timeout
            elif proc.poll() is None:
                continue
            else:
                # A child can finish between the initial drain and poll().
                for _ in range(16):
                    if not self.selector.select(0):
                        break
                    self._drain()
                try:
                    if proc.returncode:
                        raise ValueError(bytes(job['stderr']).decode(errors='replace').strip()[:240] or 'Probe exited %s' % proc.returncode)
                    def invalid_constant(value):
                        raise ValueError('Invalid JSON constant: '+value)
                    data = json.loads(job['stdout'], parse_constant=invalid_constant)
                    if not isinstance(data, dict) or data.get('error'):
                        raise ValueError(data.get('error', 'Invalid probe response') if isinstance(data, dict) else 'Invalid probe response')
                    rows = data['gpus']
                    if not isinstance(rows, list) or len(rows) > 4096 or any(not isinstance(g, dict) for g in rows):
                        raise ValueError('Invalid GPU list')
                    for g in rows:
                        if not isinstance(g.get('index'), int) or isinstance(g['index'], bool) or g['index'] < 0:
                            raise ValueError('Invalid GPU index')
                        g['status'] = classify(g, self.config.free_memory_mib)
                    if len({g['index'] for g in rows}) != len(rows):
                        raise ValueError('Duplicate GPU index')
                    result.update(gpus=sorted(rows, key=lambda g: g['index']), status='DONE', checked_at=time.time(),
                                  sample_monotonic=result['started_monotonic'])
                except (ValueError, KeyError, TypeError) as exc:
                    error = '%s: %s' % (type(exc).__name__, exc)
            if error:
                result.update(status='ERROR', error=error, gpus=[])
            self._stop(job)
        if was_running and not self.running:
            self.next_refresh = time.monotonic()+self.config.refresh_seconds

    def refresh_if_due(self, now=None):
        now = time.monotonic() if now is None else now
        if not self.running and self.next_refresh is not None and now >= self.next_refresh:
            self.start(self.config, keep_results=True)
            return True
        return False

    @property
    def running(self):
        return bool(self.jobs)

    def close(self):
        self.next_refresh = None
        for job in list(self.jobs.values()):
            self._stop(job)


def collect_cluster(config):
    with LiveScan() as scan:
        scan.start(config)
        while scan.running:
            scan.poll()
            if scan.running:
                time.sleep(.03)
        return scan.results
