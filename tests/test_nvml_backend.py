from types import SimpleNamespace as S
import pynvml as nv
from gpumonitor.nvml_backend import NVMLBackend


def fake(monkeypatch, duplicate=True):
    module = S(NVMLError=nv.NVMLError, NVMLError_NotFound=nv.NVMLError_NotFound,
        NVML_ERROR_NOT_SUPPORTED=nv.NVML_ERROR_NOT_SUPPORTED, NVML_TEMPERATURE_GPU=0,
        nvmlInit=lambda: None, nvmlShutdown=lambda: None,
        nvmlDeviceGetCount=lambda: 1, nvmlDeviceGetHandleByIndex=lambda i: i,
        nvmlDeviceGetUUID=lambda h: 'GPU-A', nvmlDeviceGetName=lambda h: b'GPU',
        nvmlDeviceGetMigMode=lambda h: (0, 0),
        nvmlDeviceGetMemoryInfo=lambda h: S(total=100, used=10),
        nvmlDeviceGetUtilizationRates=lambda h: S(gpu=1, memory=2),
        nvmlDeviceGetPowerUsage=lambda h: 25000,
        nvmlDeviceGetTemperature=lambda h, x: 30,
        nvmlDeviceGetComputeRunningProcesses=lambda h: [S(pid=10, usedGpuMemory=123)],
        nvmlDeviceGetGraphicsRunningProcesses=lambda h: [S(pid=10, usedGpuMemory=123)] if duplicate else [],
        nvmlDeviceGetProcessUtilization=lambda h, t: [])
    return module


def test_compute_graphics_dedup(monkeypatch):
    with NVMLBackend(fake(monkeypatch)) as backend:
        gpus, errors = backend.snapshot(1)
    assert not errors and gpus[0]['processes'] == [dict(pid=10, memory=123)]
    assert gpus[0]['power'] == 25


def test_sentinel_memory_unknown(monkeypatch):
    n = fake(monkeypatch, False)
    n.nvmlDeviceGetComputeRunningProcesses = lambda h: [S(pid=10, usedGpuMemory=2**64-1)]
    with NVMLBackend(n) as backend:
        gpus, _ = backend.snapshot(1)
    assert gpus[0]['processes'][0]['memory'] is None


def test_mig_unknown_utilization(monkeypatch):
    n = fake(monkeypatch)
    n.nvmlDeviceGetMigMode = lambda h: (1, 1)
    with NVMLBackend(n) as backend:
        gpus, _ = backend.snapshot(1)
    assert gpus[0]['mig'] and not gpus[0]['util_ok']


def test_not_found_is_no_evidence(monkeypatch):
    n = fake(monkeypatch)
    def missing(h, timestamp):
        raise nv.NVMLError_NotFound()
    n.nvmlDeviceGetProcessUtilization = missing
    with NVMLBackend(n) as backend:
        gpus, _ = backend.snapshot(1)
    assert gpus[0]['util_ok'] and gpus[0]['util_samples'] == []


def test_cursor_and_future_samples(monkeypatch):
    n = fake(monkeypatch)
    monkeypatch.setattr('gpumonitor.nvml_backend.time.time', lambda: 10)
    stamps = [0, 2, 2, 20]
    n.nvmlDeviceGetProcessUtilization = lambda h, t: [S(pid=10, timeStamp=i*10**6, smUtil=0, memUtil=0, encUtil=0, decUtil=0) for i in stamps]
    with NVMLBackend(n) as backend:
        first, _ = backend.snapshot(1)
        second, _ = backend.snapshot(1)
    assert len(first[0]['util_samples']) == 2
    assert not second[0]['util_samples']


def test_invalid_utilization_is_na(monkeypatch):
    n = fake(monkeypatch)
    n.nvmlDeviceGetUtilizationRates = lambda h: S(gpu=2**32-1, memory=101)
    with NVMLBackend(n) as backend:
        gpus, _ = backend.snapshot(1)
    assert gpus[0]['gpu_util'] is None and gpus[0]['memory_util'] is None
