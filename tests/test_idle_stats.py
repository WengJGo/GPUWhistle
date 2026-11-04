import csv
import io
import json
import pytest
from gpumonitor.idle_stats import idle_report
from gpumonitor.tui import idle_panel
from gpumonitor.tui_theme import width as cell_width


class Windows:
    def __init__(self, windows):
        self.data = windows

    def windows(self, since, until):
        return [w for w in self.data if since <= w['end'] <= until]


def window(start, processes, complete=True):
    return dict(start=start, end=start+10, complete=complete,
                gpus=[dict(uuid='GPU-A', index=0), dict(uuid='GPU-B', index=1)], processes=processes)


def process(user='alice', uid=1, gpu='GPU-A', status='NO_ACTIVITY_OBSERVED', identity='demo:1001', memory=2**30):
    return dict(username=user, uid=uid, gpu_uuid=gpu, status=status, identity=identity,
                valid_inactivity=status in ('NO_ACTIVITY_OBSERVED', 'SUSPECTED_IDLE'),
                memory=dict(avg=memory, min=memory, max=memory))


def test_historical_owners_remain_after_exit_and_rank_by_zero_duration(config):
    # Alice exits; only Bob is in the latest window. Alice must remain first.
    data = [window(1000, [process()]), window(1600, [process()]),
            window(2200, [process('bob', 2, status='ACTIVE')])]
    result = idle_report(Windows(data), config, now=2210)
    assert [r['username'] for r in result['rows']] == ['alice', 'bob']
    alice, bob = result['rows']
    assert alice['zero_estimated_seconds'] == alice['held_estimated_seconds'] == 1200
    assert alice['zero_sample_seconds'] == 20 and alice['last_seen'] == 1610
    assert bob['zero_estimated_seconds'] == 0 and bob['held_estimated_seconds'] == 600
    assert result['days'] == 30


def test_parallel_processes_and_mixed_active_unknown_do_not_mask_zero(config):
    ps = [process(), process(identity='demo:1002'), process(status='ACTIVE', identity='demo:1003'),
          process(status='UNKNOWN', identity='demo:1004')]
    row = idle_report(Windows([window(1000, ps)]), config, now=1010)['rows'][0]
    assert row['zero_estimated_seconds'] == 600 and row['zero_sample_seconds'] == 10
    assert row['zero_memory_avg'] == 2*2**30 and row['mixed_active_windows'] == 1
    assert row['unknown_process_windows'] == 1 and row['process_windows'] == 4
    assert row['latest_counts']['ACTIVE'] == row['latest_counts']['UNKNOWN'] == 1


@pytest.mark.parametrize('reason', ['unknown', 'active', 'low', 'invalid', 'partial', 'small', 'missing_memory', 'missing_identity'])
def test_insufficient_or_nonzero_evidence_never_adds_zero_time(config, reason):
    p = process()
    w = window(1000, [p])
    if reason in ('unknown', 'active', 'low'):
        p['status'] = {'unknown':'UNKNOWN', 'active':'ACTIVE', 'low':'LOW_ACTIVITY'}[reason]
    elif reason == 'invalid':
        p['valid_inactivity'] = False
    elif reason == 'partial':
        w['complete'] = False
    elif reason == 'small':
        p['memory']['min'] = config.idle_memory_bytes-1
    elif reason == 'missing_memory':
        p['memory']['min'] = None
    else:
        p['identity'] = None
    r = idle_report(Windows([w]), config, now=1010)['rows'][0]
    assert r['zero_estimated_seconds'] == r['zero_sample_seconds'] == r['zero_windows'] == 0
    assert r['zero_memory_avg'] is None


def test_unknown_missing_failed_windows_are_not_filled_or_extended_to_now(config):
    data = [window(1000, [process()]), window(1600, [process(status='UNKNOWN')]),
            window(2200, [], complete=False), window(3400, [process()])]
    r = idle_report(Windows(data), config, now=10000)['rows'][0]
    assert r['zero_estimated_seconds'] == 1200 and r['zero_sample_seconds'] == 20
    assert r['held_estimated_seconds'] == 1800 and r['unknown_process_windows'] == 1


def test_manual_and_duplicate_windows_use_union_instead_of_double_counting(config):
    data = [window(1000, [process()]), window(1000, [process()]), window(1005, [process()])]
    r = idle_report(Windows(data), config, now=1015)['rows'][0]
    assert r['zero_estimated_seconds'] == 605 and r['zero_sample_seconds'] == 15


def test_range_clips_sample_and_estimate_and_excludes_future(config):
    data = [window(1000, [process()]), window(1600, [process()])]
    r = idle_report(Windows(data), config, days=1, now=1005+86400)['rows'][0]
    assert r['zero_estimated_seconds'] == 605 and r['zero_sample_seconds'] == 15
    assert r['sample_coverage'] == 15/86400
    early = idle_report(Windows(data), config, now=1010)['rows'][0]
    assert early['observed_windows'] == 1


def test_filters_stable_gpu_identity_and_all_retained_history(config):
    data = [window(1000, [process(), process('bob', 2, 'GPU-B')]),
            window(1000+8*86400, [process('bob', 2, 'GPU-B', status='UNKNOWN')])]
    db = Windows(data)
    assert len(idle_report(db, config, now=1000+8*86400+10)['rows']) == 2
    assert len(idle_report(db, config, days=7, now=1000+8*86400+10)['rows']) == 1
    assert idle_report(db, config, user='alice', gpu='0', now=1010)['rows'][0]['gpu_uuid'] == 'GPU-A'
    assert idle_report(db, config, user='2', gpu='GPU-B', now=1010)['rows'][0]['username'] == 'bob'
    assert not idle_report(db, config, user='missing', now=1010)['rows']


def test_gpu_selector_includes_devices_absent_from_latest_window(config):
    old = window(1000, [process()])
    old['gpus'] = [dict(uuid='GPU-A', index=0)]
    latest = window(1600, [process(gpu='GPU-B')])
    latest['gpus'] = [dict(uuid='GPU-B', index=1)]
    result = idle_report(Windows([old, latest]), config, gpu='GPU-A', now=1610)
    assert len(result['rows']) == 1 and result['rows'][0]['last_seen'] == 1010
    assert {g['uuid'] for g in result['gpus']} == {'GPU-A', 'GPU-B'}


@pytest.mark.parametrize('width', [56, 58, 78, 98, 118, 158])
def test_statistics_width_unknown_na_and_detail(config, width):
    data = [window(1000, [process('用户名很长' * 10, status='UNKNOWN')])]
    result = idle_report(Windows(data), config, now=1010)
    lines, selected = idle_panel(result, detail=True, width=width)
    text = '\n'.join(str(line) for line, _ in lines)
    assert 'ZERO~' in text and 'PUNK' in text and 'N/A' in text and 'UNK=1' in text
    assert all(cell_width(line) <= width for line, _ in lines)
    assert selected == 1


def test_idle_cli_text_csv_json_keep_exited_users(config, rig, tmp_path, monkeypatch, capsys):
    from gpumonitor.cli import main
    from gpumonitor.storage import Storage
    clock, _, backend, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
        clock.sleep(596)
        backend.cards = {'GPU-A': []}
        db.save(sample(), config)
    monkeypatch.setattr('gpumonitor.idle_stats.time.time', clock.time)
    path = tmp_path/'config.json'
    path.write_text(json.dumps(dict(database=config.database)))
    args = ['--config', str(path), 'idle']
    assert main(args) == 0 and 'user1' in capsys.readouterr().out
    assert main(args+['--format', 'json']) == 0
    row = json.loads(capsys.readouterr().out)['rows'][0]
    assert row['zero_estimated_seconds'] == 600 and row['zero_sample_seconds'] == 4
    assert main(args+['--format', 'csv']) == 0
    row = next(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert row['username'] == 'user1' and row['zero_estimated_seconds'] == '600.0'
