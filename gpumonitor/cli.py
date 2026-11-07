import argparse
import csv
import io
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from . import __version__
from .config import load
from .storage import Storage, collection_lock
from .analytics import HISTORY_DAYS, report
from .idle_stats import idle_report


def parser():
    p = argparse.ArgumentParser(prog='seegpu' if os.path.basename(sys.argv[0]) == 'seegpu' else 'gpumonitor', description='Read-only sampled GPU user monitor')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--config', help='JSON configuration path (before subcommand)')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('doctor')
    once = sub.add_parser('once')
    once.add_argument('--duration', type=float)
    once.add_argument('--interval', type=float)
    once.add_argument('--scheduled', action='store_true', help=argparse.SUPPRESS)
    for name in ('report', 'export'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--days', type=int, choices=HISTORY_DAYS, default=1)
        cmd.add_argument('--user')
        cmd.add_argument('--gpu', help='current index or stable UUID')
        cmd.add_argument('--daily', action='store_true')
        if name == 'export':
            cmd.add_argument('--format', choices=('json', 'csv'), default='json')
    schedule = sub.add_parser('schedule')
    schedule.add_argument('action', choices=('install', 'status', 'remove'))
    schedule.add_argument('--backend', choices=('auto', 'systemd', 'cron'), default='auto')
    idle = sub.add_parser('idle', help='Rank all historical owners by estimated zero-activity occupancy')
    idle.add_argument('--days', type=int, choices=HISTORY_DAYS, default=30)
    idle.add_argument('--user')
    idle.add_argument('--gpu', help='current index or stable UUID')
    idle.add_argument('--format', choices=('text', 'json', 'csv'), default='text')
    cluster = sub.add_parser('cluster', help='Query live GPU availability on configured SSH nodes')
    cluster.add_argument('--cluster-config', help='Cluster JSON path (default: XDG gpumonitor/cluster.json)')
    cluster.add_argument('--format', choices=('text', 'json'), default='text')
    return p


def number(value, suffix='', scale=1):
    return 'N/A' if value is None else '%.1f%s' % (value / scale, suffix)


def text_report(result):
    lines = ['GPUWhistle | last %s hours | completed %s / attempted %s | planned ~%s' %
             (result['days'] * 24, result['completed_windows'], result['attempted_windows'], result['planned_windows_estimate']),
             'VRAM = window average while observed; ratios = valid observed windows.',
             'DATE       GPU          USER             AVG GiB MAX GiB ACTIVE VALID IDLE UNKNOWN PROCS PUNK']
    for row in result['rows']:
        lines.append('%-10s %-12s %-16s %7s %7s %6s %5s %4s %7s %5s %4s' % (row['date'], row['gpu_uuid'][-12:],
            row['username'][:16], number(row['memory_avg'], scale=2**30), number(row['memory_max'], scale=2**30),
            number(row['active_ratio'] * 100 if row['active_ratio'] is not None else None, '%'),
            row['valid_windows'], row['idle_windows'], row['unknown_windows'], row['distinct_processes'], row['unknown_process_windows']))
    if not result['rows']:
        lines.append('No observations. Run: gpumonitor once')
    lines.append('UNKNOWN = user windows; PUNK = UNKNOWN process-window observations, including active user windows.')
    lines.append('Sampled observations cannot establish activity between windows. Power is GPU-wide.')
    return '\n'.join(lines)


def text_idle_report(result):
    lines = ['GPUWhistle | historical idle occupancy | last %sd | descending ZERO~' % result['days'],
             'USER             GPU    ZERO~ h   SAMP s   HELD~ h  ZERO GiB  PUNK',
             'ZERO~/HELD~ = period-weighted estimates; SAMP = measured zero time.']
    for r in result['rows']:
        lines.append('%-16s %3s %10.2f %8.1f %9.2f %9s %5s' % (r['username'][:16], r['gpu_index'],
            r['zero_estimated_seconds']/3600, r['zero_sample_seconds'], r['held_estimated_seconds']/3600,
            number(r['zero_memory_avg'], scale=2**30), r['unknown_process_windows']))
    lines.append(result['note'])
    return '\n'.join(lines)


def doctor(config):
    from .nvml_backend import NVMLBackend, decoded
    from .process_info import ProcessReader
    from .scheduler import systemd_available, run
    checks = []
    def add(name, ok, detail):
        checks.append(dict(check=name, ok=ok, detail=detail))
    add('Linux', platform.system() == 'Linux', platform.platform())
    add('Python >=3.9', sys.version_info >= (3, 9), sys.version.split()[0])
    try:
        with Storage(config.database) as db:
            db.db.execute('BEGIN IMMEDIATE')
            db.db.rollback()
        add('SQLite writable', True, config.database)
    except Exception as exc:
        add('SQLite writable', False, str(exc) + '; use a writable database path')
    try:
        with NVMLBackend() as backend:
            add('NVML / driver', True, decoded(backend.n.nvmlSystemGetDriverVersion()))
            gpus, errors = backend.snapshot(time.time() - 2)
            add('GPU count', bool(gpus), str(len(gpus)) + '; ' + '; '.join(errors))
            reader = ProcessReader()
            for g in gpus:
                add('GPU %s %s' % (g['index'], g['name']), True, g['uuid'] + '; MIG=' + str(g['mig']))
                add('process utilization GPU %s' % g['index'], g['util_ok'],
                    g['capabilities'].get('process_utilization', 'N/A') + '; missing samples do not mean zero. MIG / unsupported hardware may show N/A.')
                failures = [reader.read(p['pid'])['error'] for p in g['processes']]
                failures = [e for e in failures if e]
                add('/proc GPU %s' % g['index'], g['processes_ok'] and not failures,
                    '; '.join(failures) or ('visible PIDs readable' if g['processes'] else 'no GPU processes to probe; access to other users is unverified'))
            add('/proc self', reader.read(os.getpid())['identity'] is not None,
                'hidepid / containers may restrict other users; ask administrator for read access if required')
    except Exception as exc:
        add('NVML', False, '%s: %s; install nvidia-ml-py and check the host NVIDIA driver/device permissions' % (type(exc).__name__, exc))
    user_manager = systemd_available()
    cron = shutil.which('crontab') is not None
    add('schedule capability', user_manager or cron,
        'systemd user manager=%s; crontab=%s. Run schedule install to verify installation permissions. Logout may stop user timer without linger.' % (user_manager, cron))
    if shutil.which('loginctl'):
        try:
            result = run(['loginctl', 'show-user', str(os.getuid()), '-p', 'Linger'])
            add('linger', result.returncode == 0, result.stdout.strip() or result.stderr.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    for item in checks:
        print('[%s] %s: %s' % ('OK' if item['ok'] else 'CHECK', item['check'], item['detail']))
    # Optional capabilities need not make a functional installation fail.
    return 0 if all(c['ok'] for c in checks if c['check'] in ('Linux', 'Python >=3.9', 'SQLite writable', 'NVML / driver', 'NVML', 'GPU count')) else 1


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load(args.config)
        if args.command == 'cluster':
            from .cluster import ClusterConfig, collect_cluster, gpu_status
            settings = ClusterConfig.load(args.cluster_config)
            results = collect_cluster(settings)
            for r in results:
                for g in r['gpus']:
                    g['status'] = gpu_status(g, r, settings)
                r.pop('started_monotonic')
            if args.format == 'json':
                print(json.dumps(dict(nodes=results, free_memory_mib=settings.free_memory_mib,
                    note='FREE is a point-in-time observation, not a reservation. UNKNOWN is never free.'), ensure_ascii=False, indent=2))
            else:
                print('GPUWhistle | CLUSTER LIVE | FREE: no processes, UTIL 0%%, used <= %.0f MiB' % settings.free_memory_mib)
                for r in results:
                    if r['status'] != 'DONE':
                        print('%s ERROR: %s' % (r['name'], r['error']))
                    for g in r['gpus']:
                        print('%-16s GPU %-3s %-11s FREE %s GiB / %s GiB | used %s GiB | util %s | processes %s' % (
                            r['name'], g['index'], g['status'], number(g.get('free_mib'), scale=1024),
                            number(g.get('total_mib'), scale=1024), number(g.get('used_mib'), scale=1024),
                            number(g.get('gpu_util'), '%'), g.get('process_count') if g.get('process_count') is not None else 'N/A'))
                if not results:
                    print('No cluster nodes configured. Copy cluster.example.json to ~/.config/gpumonitor/cluster.json.')
                print('Snapshot only; FREE does not reserve a GPU. UNKNOWN is never counted as free.')
            return 1 if not results or any(r['status'] == 'ERROR' for r in results) else 0
        if args.command == 'doctor':
            return doctor(config)
        if args.command == 'schedule':
            from . import scheduler
            if args.action == 'install':
                print(scheduler.install(config, args.config, args.backend))
            else:
                print(getattr(scheduler, args.action)())
            return 0
        if args.command == 'once':
            from .nvml_backend import NVMLBackend
            from .collector import collect
            if args.duration is not None:
                config.duration = args.duration
            if args.interval is not None:
                config.interval = args.interval
            config.validate()
            os.umask(0o077)
            with collection_lock(config.database), Storage(config.database) as db:
                latest = db.latest()
                if args.scheduled and latest and latest['start'] >= (time.time() // config.period) * config.period:
                    return 0
                # Persist failed NVML initialization as an attempted, invalid window.
                started = time.time()
                try:
                    with NVMLBackend() as backend:
                        window = collect(config, backend)
                except Exception as exc:
                    window = dict(start=started, end=time.time(), planned_samples=math.ceil(config.duration / config.interval), actual_samples=0,
                        successful_samples=0, complete=False, errors=['%s: %s' % (type(exc).__name__, exc)],
                        gpus=[], processes=[], users=[])
                db.save(window, config)
                print('Saved window: %s/%s successful samples; %s GPUs, %s processes; complete=%s' % (
                    window['successful_samples'], window['planned_samples'], len(window['gpus']), len(window['processes']), window['complete']))
                for error in window['errors']:
                    print('CHECK: ' + error, file=sys.stderr)
                return 0 if window['complete'] else 1
        with Storage(config.database) as db:
            if args.command == 'idle':
                result = idle_report(db, config, args.days, args.user, args.gpu)
                if args.format == 'text':
                    print(text_idle_report(result))
                elif args.format == 'json':
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    fields = [k for k in result['rows'][0] if k != 'latest_counts'] if result['rows'] else [
                        'username', 'gpu_index', 'zero_estimated_seconds', 'zero_sample_seconds', 'held_estimated_seconds',
                        'observed_seconds', 'sample_coverage', 'unknown_process_windows']
                    writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(result['rows'])
                return 0
            if args.command in ('report', 'export'):
                result = report(db, config, args.days, args.user, args.gpu, args.daily)
                if args.command == 'report':
                    print(text_report(result))
                elif args.format == 'json':
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    stream = io.StringIO()
                    fields = list(result['rows'][0]) if result['rows'] else ['date', 'gpu_uuid', 'uid', 'username', 'observed_windows', 'valid_windows', 'memory_avg', 'memory_max', 'active_windows', 'idle_windows', 'idle_valid_windows', 'unknown_windows', 'active_ratio', 'idle_ratio', 'process_windows', 'unknown_process_windows', 'windows_with_unknown_processes', 'unknown_process_ratio']
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(result['rows'])
                    print(stream.getvalue(), end='')
                return 0
            if not sys.stdin.isatty() or not sys.stdout.isatty() or not os.environ.get('TERM') or os.environ.get('TERM') == 'dumb':
                print(text_report(report(db, config)))
                return 0
            from .tui import launch
            return launch(db, config)
    except KeyboardInterrupt:
        print('Interrupted.', file=sys.stderr)
        return 130
    except Exception as exc:
        print('gpumonitor: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 1
