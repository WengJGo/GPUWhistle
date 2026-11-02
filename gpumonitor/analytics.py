import math
import time
from datetime import datetime
from .models import stats, user_key


def report(storage, config, days=1, user=None, gpu=None, daily=False, now=None):
    now = time.time() if now is None else now
    since = now - days * 86400
    windows = storage.windows(since, now)
    groups, power = {}, {}
    for w in windows:
        indexes = {g['uuid']: str(g['index']) for g in w['gpus']}
        selected = {uuid for uuid in indexes if gpu is None or str(gpu) in (uuid, indexes[uuid])}
        date = datetime.fromtimestamp(w['end']).strftime('%Y-%m-%d') if daily else ''
        for g in w['gpus']:
            if g['uuid'] in selected:
                power.setdefault(g['uuid'], []).append(dict(end=w['end'], avg_w=g['power']['avg'], peak_w=g['power']['max']))
        for u in w['users']:
            if u['gpu_uuid'] not in selected or (user is not None and user not in (u['username'], str(u['uid']))):
                continue
            key = (date, u['gpu_uuid'], u['user_key'])
            a = groups.setdefault(key, dict(date=date, gpu_uuid=u['gpu_uuid'], user_key=u['user_key'], uid=u['uid'], username=u['username'],
                observed_windows=0, valid_windows=0, active_windows=0, no_activity_windows=0,
                idle_windows=0, idle_valid_windows=0, unknown_windows=0,
                process_windows=0, unknown_process_windows=0, windows_with_unknown_processes=0, processes=set(), memories=[], peaks=[], last_seen=0))
            procs = [p for p in w['processes'] if p['gpu_uuid'] == u['gpu_uuid'] and user_key(p) == u['user_key']]
            a['gpu_index'] = indexes[u['gpu_uuid']]
            a['observed_windows'] += 1
            a['processes'].update(p['identity'] for p in procs)
            # Independent process-window evidence; do not redefine user UNKNOWN windows.
            unknown_processes = sum(p['status'] == 'UNKNOWN' for p in procs)
            a['process_windows'] += len(procs)
            a['unknown_process_windows'] += unknown_processes
            a['windows_with_unknown_processes'] += int(unknown_processes > 0)
            a['memories'].append(u['memory']['avg'])
            a['peaks'].append(u['memory']['max'])
            a['last_seen'] = max(a['last_seen'], *(p['last_seen'] for p in procs))
            active = any(p['status'] in ('ACTIVE', 'LOW_ACTIVITY') for p in procs)
            known = bool(procs) and all(p['status'] != 'UNKNOWN' for p in procs)
            if active:
                a['active_windows'] += 1
                a['valid_windows'] += 1
            elif known:
                a['valid_windows'] += 1
                a['no_activity_windows'] += 1
            else:
                a['unknown_windows'] += 1
            idle = any(p['status'] == 'SUSPECTED_IDLE' for p in procs)
            if idle:
                a['idle_windows'] += 1
            if idle or known:
                a['idle_valid_windows'] += 1
    rows = []
    for a in groups.values():
        a['distinct_processes'] = len(a.pop('processes'))
        a['memory_avg'] = stats(a.pop('memories'))['avg']
        a['memory_max'] = stats(a.pop('peaks'))['max']
        n = a['valid_windows']
        a['active_ratio'] = a['active_windows'] / n if n else None
        idle_n = a['idle_valid_windows']
        a['idle_ratio'] = a['idle_windows'] / idle_n if idle_n else None
        a['unknown_process_ratio'] = a['unknown_process_windows'] / a['process_windows'] if a['process_windows'] else None
        rows.append(a)
    return dict(days=days, since=since, until=now, planned_windows_estimate=math.floor(days * 86400 / config.period),
        attempted_windows=len(windows), completed_windows=sum(w['complete'] for w in windows),
        observed_seconds=sum(max(0, w['end'] - max(w['start'], since)) for w in windows),
        note='Planned count is an estimate using current period; no scheduler ledger. Active and idle ratios use their respective determinable user windows. '
             'VRAM averages are conditional on observed ownership; sampling is not whole-day utilization. '
             'Process UNKNOWN counts process-window observations independently of user activity. '
             'Power belongs to the entire GPU. Daily groups use local timezone.',
        rows=sorted(rows, key=lambda a: (a['date'], a['gpu_uuid'], a['username'])), gpu_power=power)
