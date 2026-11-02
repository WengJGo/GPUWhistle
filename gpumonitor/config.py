import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'gpumonitor/config.json'


def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'gpumonitor'


@dataclass
class Config:
    duration: float = 10
    interval: float = 1
    period: int = 600
    retention_days: int = 30
    idle_memory_bytes: int = 1073741824
    idle_windows: int = 3
    active_threshold: float = 10
    min_util_coverage: float = 0.6
    database: str = ''

    def validate(self):
        for key in ('duration', 'interval', 'period', 'retention_days', 'idle_windows'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('%s must be a positive finite number' % key)
        for key in ('period', 'retention_days', 'idle_windows', 'idle_memory_bytes'):
            if not isinstance(getattr(self, key), int) or isinstance(getattr(self, key), bool):
                raise ValueError('%s must be an integer' % key)
        if self.idle_memory_bytes < 0 or self.interval > self.duration or self.duration > self.period:
            raise ValueError('require interval <= duration <= period and idle_memory_bytes >= 0')
        if not 0 < self.min_util_coverage <= 1 or not 0 < self.active_threshold <= 100:
            raise ValueError('invalid utilization thresholds')
        self.database = str(Path(self.database).expanduser()) if self.database else str(data_dir() / 'history.db')
        return self


def load(path=None):
    path = Path(path) if path else config_path()
    values = json.loads(path.read_text()) if path.exists() else {}
    unknown = set(values) - set(asdict(Config()))
    if unknown:
        raise ValueError('Unknown config keys: ' + ', '.join(sorted(unknown)))
    return Config(**values).validate()
