import json
import subprocess
import sys
import time
import pytest
from gpumonitor.cluster import ClusterConfig, LiveScan, classify, gpu_status
from gpumonitor.cluster_probe import parse_xml
from gpumonitor.tui import cluster_panel
from gpumonitor.tui_theme import width as cell_width


def card(**values):
    return dict(dict(index=0, name='Test GPU', total_mib=24576, used_mib=8, free_mib=24000,
                     gpu_util=0, process_count=0, mig=False, compute_mode='Default'), **values)


def result(**values):
    return dict(dict(name='node-a', status='DONE', error=None, gpus=[dict(card(), status='FREE')],
                     started_monotonic=time.monotonic(), checked_at=time.time()), **values)


def test_free_requires_memory_utilization_and_process_evidence():
    assert classify(card(), 256) == 'FREE'
    assert classify(card(used_mib=256), 256) == 'FREE'
    assert classify(card(used_mib=257), 256) == 'IN_USE'
    assert classify(card(used_mib=0, process_count=1), 256) == 'IN_USE'
    assert classify(card(gpu_util=1), 256) == 'IN_USE'
    assert classify(card(compute_mode='Prohibited'), 256) == 'UNAVAILABLE'
    assert classify(card(free_mib=0), 256) == 'UNAVAILABLE'


@pytest.mark.parametrize('values', [
    {'used_mib':None}, {'total_mib':None}, {'free_mib':None}, {'gpu_util':None},
    {'process_count':None}, {'process_count':False}, {'mig':None}, {'mig':True}, {'compute_mode':None},
    {'used_mib':float('nan')}, {'free_mib':float('inf')}, {'gpu_util':False}, {'gpu_util':101},
    {'process_count':0.0}, {'used_mib':-1}, {'free_mib':30000}, {'total_mib':0},
])
def test_unknown_and_malformed_values_cannot_be_free(values):
    assert classify(card(**values), 256) == 'UNKNOWN'


def xml(processes='<processes/>', util='0 %'):
    return '''<nvidia_smi_log><gpu><uuid>GPU-test</uuid><minor_number>17</minor_number>
    <product_name>Test GPU</product_name><compute_mode>Default</compute_mode>
    <mig_mode><current_mig>Disabled</current_mig></mig_mode>
    <fb_memory_usage><total>24576 MiB</total><used>8 MiB</used><free>24000 MiB</free></fb_memory_usage>
    <utilization><gpu_util>%s</gpu_util></utilization>%s</gpu></nvidia_smi_log>''' % (util, processes)


def test_xml_index_is_inventory_index_not_device_minor_and_all_process_types_count():
    g = parse_xml(xml(), {'GPU-test':3})[0]
    assert g['index'] == 3 and classify(g, 256) == 'FREE'
    g = parse_xml(xml('<processes><process_info><type>G</type></process_info></processes>'), {'GPU-test':3})[0]
    assert g['process_count'] == 1 and classify(g, 256) == 'IN_USE'


@pytest.mark.parametrize('processes', ['', '<processes>N/A</processes>', '<processes><error>denied</error></processes>'])
def test_unavailable_process_list_is_not_an_empty_process_list(processes):
    g = parse_xml(xml(processes), {'GPU-test':0})[0]
    assert g['process_count'] is None and classify(g, 256) == 'UNKNOWN'


def test_xml_na_util_and_incomplete_inventory():
    assert classify(parse_xml(xml(util='N/A'), {'GPU-test':0})[0], 256) == 'UNKNOWN'
    with pytest.raises(ValueError):
        parse_xml(xml(), {'GPU-other':0})
    with pytest.raises(ValueError):
        parse_xml(xml(), {'GPU-test':0, 'GPU-other':1})
    with pytest.raises(ValueError):
        parse_xml('<error/>', {})


@pytest.mark.parametrize('data', [
    {'timeout':0}, {'timeout':True}, {'stale_after':float('nan')}, {'free_memory_mib':-1},
    {'refresh_interval':0}, {'refresh_interval':True}, {'refresh_interval':float('nan')},
    {'bad':1}, {'nodes':[{'name':'x','target':'-oProxyCommand=bad'}]},
    {'nodes':[{'name':'x','target':'host; command'}]},
    {'nodes':[{'name':'x','target':'host','extra':1}]},
    {'nodes':[{'name':'x','target':'a'},{'name':'x','target':'b'}]},
    {'nodes':[{'name':'bad\x1b','target':'a'}]},
])
def test_invalid_settings_rejected(tmp_path, data):
    p = tmp_path/'cluster.json'
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ClusterConfig.load(p)


def test_example_config_and_missing_config(tmp_path):
    from pathlib import Path
    assert len(ClusterConfig.load(Path(__file__).parents[1]/'cluster.example.json').nodes) == 3
    assert ClusterConfig.load(tmp_path/'missing.json').nodes == ()


def spawn_fake(monkeypatch, sources):
    real = subprocess.Popen
    commands = []
    def spawn(command, **kwargs):
        commands.append(command)
        return real([sys.executable, '-c', sources[len(commands)-1]], **kwargs)
    monkeypatch.setattr('gpumonitor.cluster.subprocess.Popen', spawn)
    return commands


def finish(scan):
    deadline = time.monotonic()+3
    while scan.running and time.monotonic() < deadline:
        scan.poll()
        time.sleep(.005)
    assert not scan.running


def test_parallel_nodes_partial_failure_and_ssh_command_safety(monkeypatch):
    good = 'import json; print('+repr(json.dumps({'gpus':[card()]}))+')'
    commands = spawn_fake(monkeypatch, [good, "import sys;sys.stderr.write('denied');sys.exit(255)", good])
    with LiveScan() as scan:
        scan.start(ClusterConfig(nodes=tuple(dict(name='node-'+str(n),target='node-'+str(n)) for n in range(3))))
        assert len(scan.jobs) == 3  # All children start before any result is awaited.
        finish(scan)
        assert [r['status'] for r in scan.results] == ['DONE','ERROR','DONE']
        assert scan.results[0]['gpus'][0]['status'] == 'FREE'
        assert scan.results[1]['gpus'] == [] and 'denied' in scan.results[1]['error']
    assert all('BatchMode=yes' in cmd and 'StrictHostKeyChecking=yes' in cmd for cmd in commands)


def test_large_inventory_pipes_are_drained_without_deadlock(monkeypatch):
    rows = [card(index=n, name='very long model '+('x'*80)) for n in range(200)]
    spawn_fake(monkeypatch, ['print('+repr(json.dumps({'gpus':rows}))+')'])
    with LiveScan() as scan:
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        finish(scan)
        assert len(scan.results[0]['gpus']) == 200


@pytest.mark.parametrize('response', ['bad json', '{"gpus":null}', '[]', '{"error":"NVML unavailable"}', '{"gpus":[{}]}'])
def test_probe_errors_never_leave_old_free_rows(monkeypatch, response):
    spawn_fake(monkeypatch, ['print('+repr(response)+')'])
    with LiveScan() as scan:
        scan.results = [result()]
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        assert scan.results[0]['status'] == 'SCANNING' and not scan.results[0]['gpus']
        finish(scan)
        assert scan.results[0]['status'] == 'ERROR' and not scan.results[0]['gpus']


def test_timeout_and_close_terminate_only_probe_children(monkeypatch):
    spawn_fake(monkeypatch, ['import time;time.sleep(60)', 'import time;time.sleep(60)'])
    with LiveScan() as scan:
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        child = next(iter(scan.jobs.values()))['proc']
        scan.results[0]['started_monotonic'] -= 20
        scan.poll()
        assert child.poll() is not None and 'Timed out' in scan.results[0]['error']
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        child = next(iter(scan.jobs.values()))['proc']
    assert child.poll() is not None


@pytest.mark.parametrize('width', [56, 78, 98, 118, 158])
def test_summary_free_first_color_na_staleness_and_bounded_width(width):
    with LiveScan() as scan:
        scan.results = [result(name='node-b',gpus=[dict(card(process_count=1),status='IN_USE')]), result(name='node-a')]
        lines, counts = cluster_panel(scan, width=width)
        text = '\n'.join(str(line) for line,_ in lines)
        assert 'FREE 0' in text and counts['FREE'] == 1 and counts['IN_USE'] == 1
        assert all(cell_width(line) <= width for line,_ in lines)
        assert any(role == 'load_low' for line,_ in lines if hasattr(line,'spans') for _,role in line.spans)
        r = scan.results[1]
        lines, counts = cluster_panel(scan, width=width, now=r['started_monotonic']+31)
        assert counts['FREE'] == 0 and counts['STALE'] >= 1
        summaries = '\n'.join(str(line) for line,_ in lines[1:3])
        assert 'STALE' in summaries and 'FREE none' not in summaries
        assert not any(role == 'load_low' for line,_ in lines if hasattr(line,'spans') for _,role in line.spans)
        scan.results = [result(gpus=[dict(card(used_mib=None, gpu_util=None, process_count=None),status='UNKNOWN')])]
        lines, counts = cluster_panel(scan, width=width)
        assert 'N/A' in '\n'.join(str(line) for line,_ in lines) and counts['UNKNOWN'] == 1


def test_cli_cluster_does_not_create_or_write_history(tmp_path, monkeypatch, capsys):
    from gpumonitor.cli import main
    p = tmp_path/'config.json'
    database = tmp_path/'must-not-exist.db'
    p.write_text(json.dumps({'database':str(database)}))
    cp = tmp_path/'cluster.json'
    cp.write_text(json.dumps({'nodes':[{'name':'node-a','target':'node-a'}]}))
    monkeypatch.setattr('gpumonitor.cluster.collect_cluster', lambda settings:[result()])
    args = ['--config',str(p),'cluster','--cluster-config',str(cp)]
    assert main(args+['--format','json']) == 0
    assert json.loads(capsys.readouterr().out)['nodes'][0]['gpus'][0]['status'] == 'FREE'
    assert main(args) == 0 and 'FREE' in capsys.readouterr().out
    assert not database.exists()


def test_automatic_refresh_updates_occupancy_without_resetting_old_sample_age(monkeypatch):
    good = lambda g: 'print('+repr(json.dumps({'gpus':[g]}))+')'
    spawn_fake(monkeypatch, [good(card()), good(card(process_count=1)), good(card())])
    config = ClusterConfig(nodes=({'name':'node-a','target':'node-a'},))
    with LiveScan() as scan:
        scan.start(config)
        finish(scan)
        old = scan.results[0]
        sample_time = old['sample_monotonic']
        assert not scan.refresh_if_due(now=scan.next_refresh-.01)
        assert scan.refresh_if_due(now=scan.next_refresh)
        pending = scan.results[0]
        assert pending['status'] == 'REFRESHING' and pending['gpus'][0]['status'] == 'FREE'
        assert pending['sample_monotonic'] == sample_time
        assert gpu_status(pending['gpus'][0], pending, config, now=sample_time+31) == 'STALE'
        finish(scan)
        assert scan.results[0]['gpus'][0]['status'] == 'IN_USE'
        assert scan.refresh_if_due(now=scan.next_refresh)
        finish(scan)
        assert scan.results[0]['gpus'][0]['status'] == 'FREE'


def test_automatic_refresh_failure_clears_green_and_recovers(monkeypatch):
    good = 'print('+repr(json.dumps({'gpus':[card()]}))+')'
    spawn_fake(monkeypatch, [good, "raise SystemExit('offline')", good])
    with LiveScan() as scan:
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        finish(scan)
        scan.refresh_if_due(now=scan.next_refresh)
        finish(scan)
        assert scan.results[0]['status'] == 'ERROR' and scan.results[0]['gpus'] == []
        scan.refresh_if_due(now=scan.next_refresh)
        assert scan.results[0]['status'] == 'SCANNING'
        finish(scan)
        assert scan.results[0]['status'] == 'DONE' and scan.results[0]['gpus'][0]['status'] == 'FREE'


def test_automatic_refresh_does_not_overlap_and_close_disables_it(monkeypatch):
    good = 'print('+repr(json.dumps({'gpus':[card()]}))+')'
    commands = spawn_fake(monkeypatch, [good, 'import time;time.sleep(60)'])
    with LiveScan() as scan:
        scan.start(ClusterConfig(nodes=({'name':'node-a','target':'node-a'},)))
        finish(scan)
        scan.refresh_if_due(now=scan.next_refresh)
        assert not scan.refresh_if_due(now=scan.next_refresh+100)
        assert len(commands) == 2
        scan.close()
        assert not scan.refresh_if_due(now=time.monotonic()+100) and not scan.running


def test_short_expiry_also_shortens_refresh_interval():
    assert ClusterConfig(stale_after=2).refresh_seconds == 1
    assert ClusterConfig().refresh_seconds == 10
