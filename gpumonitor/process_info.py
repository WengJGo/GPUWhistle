import os
import pwd
from pathlib import Path


class ProcessReader:
    def __init__(self, root='/proc'):
        self.root = Path(root)
        try:
            self.boot_id = (self.root / 'sys/kernel/random/boot_id').read_text().strip()
            self.boot_time = int(next(line.split()[1] for line in (self.root / 'stat').read_text().splitlines() if line.startswith('btime ')))
        except (OSError, StopIteration, ValueError):
            self.boot_id, self.boot_time = None, None
        self.ticks = os.sysconf('SC_CLK_TCK')

    def read(self, pid):
        result = dict(pid=pid, uid=None, username='unknown', name='unknown', state='?',
                      start_time=None, identity=None, error=None)
        try:
            path = self.root / str(pid)
            first = (path / 'stat').read_text()
            fields = first[first.rindex(')') + 2:].split()
            start_ticks = int(fields[19])
            uid = int(next(line.split()[1] for line in (path / 'status').read_text().splitlines() if line.startswith('Uid:')))
            second = (path / 'stat').read_text()
            if second[second.rindex(')') + 2:].split()[19] != str(start_ticks):
                raise ProcessLookupError('PID reused while reading /proc')
            result.update(uid=uid, name=first[first.index('(') + 1:first.rindex(')')], state=fields[0])
            try:
                result['username'] = pwd.getpwuid(uid).pw_name
            except KeyError:
                result['username'] = str(uid)
            if self.boot_id is not None and self.boot_time is not None:
                result.update(identity='%s:%s:%s' % (self.boot_id, pid, start_ticks),
                              start_time=self.boot_time + start_ticks / self.ticks)
            else:
                result['error'] = 'boot identity unavailable'
        except (OSError, ValueError, IndexError, StopIteration) as exc:
            result['error'] = '%s: %s' % (type(exc).__name__, exc)
        return result
