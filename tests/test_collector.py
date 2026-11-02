from gpumonitor.collector import collect
from conftest import Clock, Backend, Reader


def test_single_gpu_user(rig):
    _, _, _, sample = rig
    w = sample()
    assert w['complete'] and w['actual_samples'] == 4
    assert len(w['processes']) == 1
    assert w['processes'][0]['status'] == 'NO_ACTIVITY_OBSERVED'
    assert w['users'][0]['memory']['avg'] == 2**30


def test_multi_gpu_multi_user_and_same_user_processes(rig):
    _, _, backend, sample = rig
    backend.cards = {'GPU-A': [10, 11, 20], 'GPU-B': [20, 30]}
    backend.utilization = 3
    w = sample()
    assert len(w['gpus']) == 2 and len(w['processes']) == 5 and len(w['users']) == 4
    user = next(u for u in w['users'] if u['uid'] == 1)
    assert user['memory']['avg'] == 2 * 2**30
    assert all(p['sm']['avg'] == 3 and p['status'] == 'LOW_ACTIVITY' for p in w['processes'])


def test_unsupported_is_unknown(rig):
    _, _, backend, sample = rig
    backend.unsupported = True
    assert sample()['processes'][0]['status'] == 'UNKNOWN'


def test_missing_nonzero_samples_not_zero(rig):
    _, _, backend, sample = rig
    backend.no_samples = True
    p = sample()['processes'][0]
    assert p['status'] == 'UNKNOWN' and p['sm']['avg'] is None


def test_exit_mid_window(rig):
    _, _, backend, sample = rig
    backend.hook = lambda step: backend.cards.update({'GPU-A': []}) if step == 3 else None
    w = sample()
    assert w['complete']
    assert w['processes'][0]['observations'] == 2
    assert w['processes'][0]['status'] == 'UNKNOWN'


def test_pid_reuse_splits_identity(rig):
    clock, reader, backend, sample = rig
    backend.hook = lambda step: reader.version.update({10: int(clock.time())}) if step == 3 else None
    w = sample()
    assert len(w['processes']) == 2
    assert all(p['status'] == 'UNKNOWN' for p in w['processes'])


def test_permission_denied_retains_pid_gpu(rig):
    _, reader, _, sample = rig
    reader.denied.add(10)
    w = sample()
    assert all(p['pid'] == 10 and p['username'] == 'unknown' and p['gpu_uuid'] == 'GPU-A'
        and p['status'] == 'UNKNOWN' for p in w['processes'])


def test_idle_cards_have_no_users(rig):
    _, _, backend, sample = rig
    backend.cards = {'GPU-A': [], 'GPU-B': []}
    w = sample()
    assert w['complete'] and len(w['gpus']) == 2 and not w['users']


def test_partial_failure_cannot_mark_idle(rig):
    _, _, backend, sample = rig
    backend.fail = {2}
    w = sample()
    assert not w['complete'] and w['successful_samples'] == 3
    assert w['processes'][0]['status'] == 'UNKNOWN'


def test_timestamp_dedup_and_boundaries(rig):
    clock, _, backend, sample = rig
    original = backend.snapshot
    start = clock.time()
    def snapshot(since):
        gpus, errors = original(since)
        for g in gpus:
            first = dict(g['util_samples'][0])
            # Duplicate zero, historical active, future active must not bias activity.
            stale = dict(first, timestamp=int((start-10)*1e6), sm=90)
            future = dict(first, timestamp=int((start+100)*1e6), sm=90)
            g['util_samples'] += [first, stale, future]
        return gpus, errors
    backend.snapshot = snapshot
    p = sample()['processes'][0]
    assert p['util_samples'] == 4 and p['status'] == 'NO_ACTIVITY_OBSERVED'


def test_only_one_sample_lacks_coverage(rig):
    clock, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        gpus, errors = original(since)
        for g in gpus:
            if backend.steps != 1:
                g['util_samples'] = []
        return gpus, errors
    backend.snapshot = snapshot
    assert sample()['processes'][0]['status'] == 'UNKNOWN'


def test_deadlines_do_not_drift(config):
    clock = Clock()
    backend = Backend(clock)
    original = backend.snapshot
    def snapshot(since):
        result = original(since)
        clock.sleep(.2)
        return result
    backend.snapshot = snapshot
    collect(config, backend, Reader(), clock, clock.time)
    assert abs(clock.time() - 1000004.2) < 1e-6


def test_failure_one_card_keeps_other(rig):
    _, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        gpus, _ = original(since)
        return gpus, ['GPU B lost']
    backend.snapshot = snapshot
    w = sample()
    assert len(w['gpus']) == 1 and not w['complete']


def test_sparse_endpoints_not_sufficient_coverage(rig, config):
    config.duration = 10
    _, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        gpus, errors = original(since)
        if backend.steps not in (1, 10):
            gpus[0]['util_samples'] = []
        return gpus, errors
    backend.snapshot = snapshot
    p = sample()['processes'][0]
    assert p['status'] == 'UNKNOWN' and p['util_coverage'] == .2


def test_slow_final_read_invalidates_window(rig):
    clock, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        result = original(since)
        if backend.steps == 4:
            clock.sleep(5)
        return result
    backend.snapshot = snapshot
    w = sample()
    assert not w['complete'] and w['processes'][0]['status'] == 'UNKNOWN'


def test_mixed_process_sm_unknown_is_not_zero(mixed_rig):
    _, sample = mixed_rig
    procs = {p['pid']: p for p in sample()['processes']}
    active, unknown = procs[1101], procs[1102]
    assert active['status'] == 'ACTIVE'
    assert active['sm']['avg'] == 98.5 and active['sm']['max'] == 99
    assert active['sm_samples'] == 4 and active['sm_coverage'] == .75
    assert unknown['status'] == 'UNKNOWN'
    assert unknown['sm']['avg'] is unknown['sm']['max'] is None
    assert unknown['sm_samples'] == 0 and unknown['sm_coverage'] == 0


def test_sm_coverage_excludes_invalid_sm_values(rig):
    _, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        gpus, errors = original(since)
        if backend.steps in (2, 3):
            gpus[0]['util_samples'][0]['sm'] = None
        return gpus, errors
    backend.snapshot = snapshot
    p = sample()['processes'][0]
    assert p['status'] == 'UNKNOWN' and not p['valid_inactivity']
    assert p['util_coverage'] == .75 and p['sm_coverage'] == .5 and p['sm_samples'] == 2
