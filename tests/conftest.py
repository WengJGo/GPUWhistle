import pytest
from gpumonitor.config import Config
from gpumonitor.collector import collect


class Clock:
    def __init__(self, start=1000000):
        self.value = start
    def monotonic(self):
        return self.value
    def time(self):
        return self.value
    def sleep(self, duration):
        self.value += duration


class Reader:
    def __init__(self):
        self.version = {}
        self.denied = set()
    def read(self, pid):
        denied = pid in self.denied
        return dict(pid=pid, uid=None if denied else pid // 10, username='unknown' if denied else 'user%s' % (pid // 10),
            name='python', state='S', start_time=None if denied else 1 + self.version.get(pid, 0),
            identity=None if denied else 'boot:%s:%s' % (pid, self.version.get(pid, 0)), error='denied' if denied else None)


class Backend:
    def __init__(self, clock, cards=None, utilization=0):
        self.clock = clock
        self.cards = cards if cards is not None else {'GPU-A': [10]}
        self.utilization = utilization
        self.steps = 0
        self.unsupported = False
        self.no_samples = False
        self.fail = set()
        self.hook = None
    def snapshot(self, since):
        self.steps += 1
        if self.hook:
            self.hook(self.steps)
        if self.steps in self.fail:
            raise RuntimeError('GPU read failed')
        result = []
        for index, (uuid, pids) in enumerate(self.cards.items()):
            result.append(dict(uuid=uuid, index=index, name='Fake GPU', memory_total=24 * 2**30, mig=False,
                memory_used=len(pids)*2**30, gpu_util=self.utilization, memory_util=0, power=20,
                temperature=35, processes_ok=True, util_ok=not self.unsupported,
                capabilities={'process_utilization': 'unsupported' if self.unsupported else 'supported'}, errors=[],
                processes=[dict(pid=pid, memory=2**30) for pid in pids],
                util_samples=[] if self.unsupported or self.no_samples else [dict(pid=pid,
                    timestamp=int(self.clock.time()*1e6), sm=self.utilization, memory_util=0,
                    encoder=0, decoder=0) for pid in pids]))
        return result, []


@pytest.fixture
def config(tmp_path):
    return Config(duration=4, interval=1, database=str(tmp_path / 'history.db')).validate()


@pytest.fixture
def rig(config):
    clock, reader = Clock(), Reader()
    backend = Backend(clock)
    def sample():
        return collect(config, backend, reader, clock=clock, wall=clock.time)
    return clock, reader, backend, sample


@pytest.fixture
def mixed_rig(rig):
    """One user's busy Ray worker and unobservable vLLM worker, same GPU."""
    clock, reader, backend, sample = rig
    backend.cards = {'GPU-A': [1101, 1102]}
    read = reader.read
    reader.read = lambda pid: dict(read(pid), uid=0, username='root',
        name='train_worker' if pid == 1101 else 'model_worker')
    snapshot = backend.snapshot
    def mixed_snapshot(since):
        gpus, errors = snapshot(since)
        for gpu in gpus:
            for process in gpu['processes']:
                process['memory'] = 12 * 2**30 if process['pid'] == 1101 else 2**30
            gpu['util_samples'] = [dict(s, sm=98 if backend.steps % 2 else 99)
                                   for s in gpu['util_samples'] if s['pid'] == 1101]
        return gpus, errors
    backend.snapshot = mixed_snapshot
    return clock, sample
