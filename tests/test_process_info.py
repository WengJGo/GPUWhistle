import os
from gpumonitor.process_info import ProcessReader


def test_self_stable_identity():
    reader = ProcessReader()
    first, second = reader.read(os.getpid()), reader.read(os.getpid())
    assert first['identity'] == second['identity'] and first['identity']
    assert first['uid'] == os.getuid() and first['start_time'] > 0


def test_missing_process_unknown():
    result = ProcessReader().read(999999999)
    assert result['identity'] is None and result['username'] == 'unknown' and result['error']
