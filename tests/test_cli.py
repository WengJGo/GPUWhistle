import json
import pytest
from gpumonitor.cli import main
from gpumonitor.storage import Storage


def config_args(config, tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'database': config.database}))
    return ['--config', str(path)]


def test_non_tty_empty(config, tmp_path, capsys):
    assert main(config_args(config, tmp_path)) == 0
    assert 'gpumonitor once' in capsys.readouterr().out


def test_export_report_and_filters(config, tmp_path, rig, capsys):
    _, _, _, sample = rig
    w = sample()
    # CLI uses real current time; shift timestamps only for export range selection.
    import time
    shift = time.time() - w['end']
    w['start'] += shift
    w['end'] += shift
    with Storage(config.database) as db:
        db.save(w, config)
    args = config_args(config, tmp_path)
    assert main(args + ['export', '--format', 'json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['rows'][0]['username'] == 'user1'
    assert main(args + ['export', '--format', 'csv']) == 0
    assert 'gpu_uuid' in capsys.readouterr().out
    assert main(args + ['report', '--days', '7', '--user', 'other']) == 0
    assert 'No observations' in capsys.readouterr().out


def test_invalid_config_friendly(tmp_path, capsys):
    path = tmp_path / 'bad.json'
    path.write_text('{"interval": 0}')
    assert main(['--config', str(path), 'once']) == 1
    assert 'Traceback' not in capsys.readouterr().err


def test_missing_nvml_saved_failure(config, tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise ImportError('mock missing nvml')
    monkeypatch.setattr('gpumonitor.nvml_backend.NVMLBackend', fail)
    assert main(config_args(config, tmp_path) + ['once']) == 1
    with Storage(config.database) as db:
        assert not db.latest()['complete'] and 'mock missing nvml' in db.latest()['errors'][0]


def test_mixed_unknown_metrics_in_text_csv_and_json(config, tmp_path, mixed_rig, capsys, monkeypatch):
    import csv
    import io
    from gpumonitor import analytics
    clock, sample = mixed_rig
    with Storage(config.database) as db:
        db.save(sample(), config)
    monkeypatch.setattr(analytics.time, 'time', clock.time)
    args = config_args(config, tmp_path)
    assert main(args + ['report']) == 0
    text = capsys.readouterr().out
    assert 'PUNK' in text and '100.0%' in text
    assert main(args + ['export', '--format', 'json']) == 0
    row = json.loads(capsys.readouterr().out)['rows'][0]
    assert row['unknown_windows'] == 0 and row['unknown_process_windows'] == 1
    assert row['unknown_process_ratio'] == .5
    assert main(args + ['export', '--format', 'csv']) == 0
    row = next(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert row['unknown_windows'] == '0' and row['unknown_process_windows'] == '1'
    assert row['process_windows'] == '2' and row['windows_with_unknown_processes'] == '1'


@pytest.mark.parametrize('days', [15, 30])
def test_long_history_ranges_report_export_and_idle(config, tmp_path, rig, capsys, monkeypatch, days):
    clock, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
    monkeypatch.setattr('gpumonitor.analytics.time.time', clock.time)
    args = config_args(config, tmp_path)
    assert main(args+['report', '--days', str(days)]) == 0
    assert 'last %s hours' % (days*24) in capsys.readouterr().out
    for command in ('export', 'idle'):
        assert main(args+[command, '--days', str(days), '--format', 'json']) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['days'] == days and result['attempted_windows'] == 1
        assert len(result['rows']) == 1
        assert main(args+[command, '--days', str(days), '--format', 'csv']) == 0
        assert 'user1' in capsys.readouterr().out
