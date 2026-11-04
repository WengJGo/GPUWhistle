import pytest
from gpumonitor.analytics import report
from gpumonitor.storage import Storage


@pytest.mark.parametrize('days', [1, 3, 7, 15, 30])
def test_range_boundary(rig, config, days):
    clock, _, _, sample = rig
    w = sample()
    with Storage(config.database) as db:
        db.save(w, config)
        assert len(report(db, config, days, now=w['end']+days*86400)['rows']) == 1
        assert not report(db, config, days, now=w['end']+days*86400+.001)['rows']


def test_denominator_absence_unknown_and_memory(rig, config):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-A': [10, 11]}
    with Storage(config.database) as db:
        backend.utilization = 50
        db.save(sample(), config)
        clock.sleep(596)
        backend.no_samples = True
        db.save(sample(), config)
        clock.sleep(596)
        backend.cards = {'GPU-A': []}
        db.save(sample(), config)
        result = report(db, config, now=clock.time())
        row = result['rows'][0]
        assert row['memory_avg'] == row['memory_max'] == 2*2**30
        assert row['observed_windows'] == 2 and row['valid_windows'] == 1
        assert row['active_ratio'] == 1 and row['unknown_windows'] == 1
        assert row['distinct_processes'] == 2
        assert result['attempted_windows'] == 3 and result['completed_windows'] == 3


def test_daily_and_filters(rig, config):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-A': [10], 'GPU-B': [20]}
    with Storage(config.database) as db:
        db.save(sample(), config)
        clock.sleep(86400)
        db.save(sample(), config)
        result = report(db, config, days=3, user='user1', gpu='0', daily=True, now=clock.time())
        assert len(result['rows']) == 2
        assert all(row['username'] == 'user1' and row['date'] for row in result['rows'])
        assert list(result['gpu_power']) == ['GPU-A']


def test_mixed_unknown_idle_uses_separate_valid_denominator(rig, config):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-A': [10, 11]}
    with Storage(config.database) as db:
        w = sample()
        db.save(w, config)
        saved = db.latest()
        saved['processes'][0]['status'] = 'SUSPECTED_IDLE'
        saved['processes'][1]['status'] = 'UNKNOWN'
        import json
        db.db.execute('UPDATE windows SET payload=?', (json.dumps(saved),))
        db.db.commit()
        row = report(db, config, now=clock.time())['rows'][0]
        assert row['valid_windows'] == 0 and row['active_ratio'] is None
        assert row['idle_windows'] == row['idle_valid_windows'] == 1 and row['idle_ratio'] == 1


@pytest.mark.parametrize('days', [1, 3, 7, 15, 30])
@pytest.mark.parametrize('daily', [False, True])
def test_mixed_active_unknown_preserves_user_windows_and_counts_process_windows(mixed_rig, config, days, daily):
    clock, sample = mixed_rig
    with Storage(config.database) as db:
        db.save(sample(), config)
        clock.sleep(596)
        db.save(sample(), config)
        row = report(db, config, days=days, daily=daily, user='root', gpu='0', now=clock.time())['rows'][0]
    # User activity is established; this does not establish every process's state.
    assert row['active_windows'] == row['valid_windows'] == row['observed_windows'] == 2
    assert row['active_ratio'] == 1 and row['unknown_windows'] == row['idle_windows'] == row['no_activity_windows'] == 0
    assert row['process_windows'] == 4 and row['unknown_process_windows'] == 2
    assert row['windows_with_unknown_processes'] == 2 and row['unknown_process_ratio'] == .5
    assert row['distinct_processes'] == 2


def test_process_unknown_counts_remain_separate_for_all_unknown_window(rig, config):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-A': [10, 11]}
    backend.no_samples = True
    with Storage(config.database) as db:
        db.save(sample(), config)
        row = report(db, config, now=clock.time())['rows'][0]
    assert row['unknown_windows'] == 1 and row['active_ratio'] is None
    assert row['unknown_process_windows'] == row['process_windows'] == 2
    assert row['unknown_process_ratio'] == 1 and row['windows_with_unknown_processes'] == 1
