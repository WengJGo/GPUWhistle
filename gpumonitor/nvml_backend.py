"""Thin NVML adapter. No CUDA import, context, kernel or process signals."""
import importlib
import time


def decoded(value):
    return value.decode(errors='replace') if isinstance(value, bytes) else str(value)


def metric(value):
    # NVML_VALUE_NOT_AVAILABLE and unsigned sentinels are not measurements.
    return value if isinstance(value, (int, float)) and 0 <= value < (1 << 63) else None


def percent(value):
    value = metric(value)
    return value if value is not None and value <= 100 else None


class NVMLBackend:
    def __init__(self, module=None):
        self.n = module or importlib.import_module('pynvml')
        self.opened = False
        self.cursors = {}

    def __enter__(self):
        self.n.nvmlInit()
        self.opened = True
        return self

    def __exit__(self, *args):
        if self.opened:
            self.n.nvmlShutdown()
            self.opened = False

    def snapshot(self, since):
        n = self.n
        output, errors = [], []
        count = n.nvmlDeviceGetCount()
        for index in range(count):
            try:
                h = n.nvmlDeviceGetHandleByIndex(index)
                uuid = decoded(n.nvmlDeviceGetUUID(h))
                g = dict(index=index, uuid=uuid, name=decoded(n.nvmlDeviceGetName(h)),
                         processes=[], util_samples=[], capabilities={}, errors=[])
                def query(label, function, *args):
                    try:
                        value = function(*args)
                        g['capabilities'][label] = 'supported'
                        return value
                    except (n.NVMLError, AttributeError) as exc:
                        g['capabilities'][label] = type(exc).__name__ + ': ' + str(exc)
                        g['errors'].append(label + ': ' + str(exc))
                        return None
                mig = query('mig', lambda: n.nvmlDeviceGetMigMode(h))
                g['mig'] = bool(mig and mig[0])
                memory = query('memory', n.nvmlDeviceGetMemoryInfo, h)
                g['memory_total'] = metric(memory.total) if memory else None
                g['memory_used'] = metric(memory.used) if memory else None
                util = query('utilization', n.nvmlDeviceGetUtilizationRates, h)
                g['gpu_util'] = percent(util.gpu) if util else None
                g['memory_util'] = percent(util.memory) if util else None
                power = query('power', n.nvmlDeviceGetPowerUsage, h)
                g['power'] = power / 1000 if metric(power) is not None else None
                g['temperature'] = query('temperature', n.nvmlDeviceGetTemperature, h, n.NVML_TEMPERATURE_GPU)
                procs = {}
                g['processes_ok'] = True
                for label, fname in (('compute', 'nvmlDeviceGetComputeRunningProcesses'), ('graphics', 'nvmlDeviceGetGraphicsRunningProcesses')):
                    rows = query(label, lambda: getattr(n, fname)(h))
                    if rows is None:
                        g['processes_ok'] = False
                    for p in rows or []:
                        mem = None if g['mig'] else metric(p.usedGpuMemory)
                        old = procs.get(p.pid)
                        # compute/graphics may both describe the same allocation.
                        if old is None or (mem is not None and (old['memory'] is None or mem > old['memory'])):
                            procs[p.pid] = dict(pid=p.pid, memory=mem)
                g['processes'] = list(procs.values())
                cursor = self.cursors.get(uuid, int(since * 1e6))
                try:
                    if g['mig']:
                        raise n.NVMLError(n.NVML_ERROR_NOT_SUPPORTED)
                    samples = n.nvmlDeviceGetProcessUtilization(h, cursor)
                    g['capabilities']['process_utilization'] = 'supported'
                    g['util_ok'] = True
                except n.NVMLError as exc:
                    if isinstance(exc, n.NVMLError_NotFound) or getattr(exc, "value", None) == 0:
                        samples = []
                        g['util_ok'] = True
                        g['capabilities']['process_utilization'] = 'no samples (not zero)'
                    else:
                        samples = []
                        g['util_ok'] = False
                        g['capabilities']['process_utilization'] = type(exc).__name__ + ': ' + str(exc)
                        g['errors'].append('process_utilization: ' + str(exc))
                except AttributeError as exc:
                    samples = []
                    g['util_ok'] = False
                    g['capabilities']['process_utilization'] = str(exc)
                now_us = int(time.time() * 1e6)
                for s in samples:
                    if int(since * 1e6) <= s.timeStamp <= now_us and s.timeStamp > cursor:
                        g['util_samples'].append(dict(pid=s.pid, timestamp=s.timeStamp,
                            sm=percent(s.smUtil), memory_util=percent(s.memUtil),
                            encoder=percent(s.encUtil), decoder=percent(s.decUtil)))
                if g['util_samples']:
                    self.cursors[uuid] = max(s['timestamp'] for s in g['util_samples'])
                output.append(g)
            except (n.NVMLError, AttributeError) as exc:
                errors.append('GPU %s: %s: %s' % (index, type(exc).__name__, exc))
        return output, errors
