"""Rank memory-holding processes by sampled zero-activity evidence.

Each observation represents one sampling period, including historical owners
whose processes have exited. Estimates are not proof of continuous inactivity.
"""
import time
from .models import stats, user_key


def _seconds(intervals):
    total, end = 0.0, float('-inf')
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def idle_report(storage, config, days=30, user=None, gpu=None, now=None):
    now = time.time() if now is None else now
    since = now - days * 86400
    windows = storage.windows(since, now)
    groups, devices = {}, {}
    for w in windows:
        devices.update((g['uuid'], dict(uuid=g['uuid'], index=g['index'])) for g in w['gpus'])
        indexes = {g['uuid']: str(g['index']) for g in w['gpus']}
        owners = {}
        for p in w['processes']:
            uuid = p['gpu_uuid']
            if gpu is not None and str(gpu) not in (uuid, indexes.get(uuid)):
                continue
            if user is not None and user not in (p['username'], str(p['uid'])):
                continue
            owners.setdefault((uuid, user_key(p)), []).append(p)
        start, end = max(since, w['start']), min(now, w['end'])
        # Attribute a period ending at the observation, without extending into
        # the future. Union intervals so manual/overlapping windows cannot inflate it.
        period_start = max(since, end - config.period)
        for key, procs in owners.items():
            a = groups.setdefault(key, dict(gpu_uuid=key[0], user_key=key[1], uid=procs[0]['uid'],
                username=procs[0]['username'], observed_windows=0, zero_windows=0, idle_windows=0,
                mixed_active_windows=0, process_windows=0, unknown_process_windows=0,
                zero=[], estimated=[], observed=[], held=[], memories=[], last_zero=None))
            a.update(gpu_index=indexes.get(key[0], '?'), username=procs[0]['username'],
                last_seen=w['end'], latest_counts=dict((status, sum(p['status'] == status for p in procs))
                    for status in ('ACTIVE', 'LOW_ACTIVITY', 'NO_ACTIVITY_OBSERVED', 'SUSPECTED_IDLE', 'UNKNOWN')))
            a['observed_windows'] += 1
            a['process_windows'] += len(procs)
            a['unknown_process_windows'] += sum(p['status'] == 'UNKNOWN' for p in procs)
            a['observed'].append((start, end))
            a['held'].append((period_start, end))
            eligible = [p for p in procs if w['complete'] and p.get('valid_inactivity')
                and p['status'] in ('NO_ACTIVITY_OBSERVED', 'SUSPECTED_IDLE')
                and p['memory']['min'] is not None and p['memory']['min'] >= config.idle_memory_bytes
                and p.get('identity') is not None]
            if eligible and end > start:
                a['zero_windows'] += 1
                a['idle_windows'] += int(any(p['status'] == 'SUSPECTED_IDLE' for p in eligible))
                a['mixed_active_windows'] += int(any(p['status'] in ('ACTIVE', 'LOW_ACTIVITY') for p in procs))
                a['zero'].append((start, end))
                a['estimated'].append((period_start, end))
                a['memories'].append(sum(p['memory']['avg'] for p in eligible if p['memory']['avg'] is not None))
                a['last_zero'] = w['end']
    rows = []
    for a in groups.values():
        a['zero_sample_seconds'] = _seconds(a.pop('zero'))
        a['zero_estimated_seconds'] = _seconds(a.pop('estimated'))
        a['observed_seconds'] = _seconds(a.pop('observed'))
        a['held_estimated_seconds'] = _seconds(a.pop('held'))
        a['sample_coverage'] = a['observed_seconds'] / (days * 86400)
        a['zero_memory_avg'] = stats(a.pop('memories'))['avg']
        rows.append(a)
    rows.sort(key=lambda a: (-a['zero_estimated_seconds'], -a['zero_sample_seconds'], a['username'], a['gpu_uuid'], a['user_key']))
    return dict(days=days, since=since, until=now, attempted_windows=len(windows),
        completed_windows=sum(bool(w['complete']) for w in windows), rows=rows, gpus=list(devices.values()),
        period_seconds=config.period, idle_memory_bytes=config.idle_memory_bytes,
        note='ZERO~ assigns one current sampling period ending at each reliable zero-activity observation; '
             'intervals are clipped to the selected range and overlaps counted once per user/GPU. '
             'HELD~ uses the same estimate for all observed ownership, including UNKNOWN. '
             'Historical owners remain after exit. Unobserved gaps are not filled. '
             'ZERO SAMP is measured window time, not process-seconds. Parallel processes are counted once per user/GPU. '
             'Only memory >= the configured idle threshold qualifies. NOACT and suspected IDLE both qualify; '
             'ACTIVE/LOW/UNKNOWN never qualify. Mixed users may still have other active processes. '
             'UNKNOWN observations add no zero time. Coverage is observed owner-window time / selected range. '
             'Sampling cannot prove inactivity between windows; estimates are not confirmed continuous idle time.')
