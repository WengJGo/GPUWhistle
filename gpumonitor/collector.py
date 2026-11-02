import math
import time
from .models import stats, user_key
from .process_info import ProcessReader


def collect(config, backend, reader=None, clock=time, wall=time.time):
    """Deadlines are monotonic; dropped deadlines are counted, never caught up in a burst."""
    reader = reader or ProcessReader()
    started = wall()
    origin = clock.monotonic()
    expected = max(1, math.ceil(config.duration / config.interval))
    window = dict(start=started, end=started, planned_samples=expected, actual_samples=0,
                  successful_samples=0, complete=False, errors=[], gpus=[], processes=[], users=[])
    gpu_rows, proc_rows, user_memory, util_rows = {}, {}, {}, {}
    for step in range(1, expected + 1):
        deadline = origin + min(step * config.interval, config.duration)
        clock.sleep(max(0, deadline - clock.monotonic()))
        # If reading took a whole extra interval, record missing samples instead of drifting.
        if clock.monotonic() > deadline + config.interval and step < expected:
            window['errors'].append('missed sampling deadline %s' % step)
            continue
        window['actual_samples'] += 1
        try:
            gpus, errors = backend.snapshot(started)
            window['errors'].extend(errors)
            if not errors and all(g['processes_ok'] for g in gpus):
                window['successful_samples'] += 1
        except Exception as exc:
            window['errors'].append('snapshot: %s: %s' % (type(exc).__name__, exc))
            continue
        observed = wall()
        for g in gpus:
            uuid = g['uuid']
            bucket = gpu_rows.setdefault(uuid, [])
            bucket.append(g)
            util_rows.setdefault(uuid, []).extend(g['util_samples'])
            totals = {}
            for p in g['processes']:
                info = reader.read(p['pid'])
                identity = info['identity']
                if identity is None:
                    # An unverifiable PID never acquires continuity, even inside a window.
                    identity = 'unverified:%s:%s:%s' % (started, step, p['pid'])
                key = (uuid, identity)
                row = proc_rows.setdefault(key, dict(info, identity=identity, gpu_uuid=uuid,
                    first_seen=observed, last_seen=observed, memories=[], observations=0, trusted=info['identity'] is not None))
                row['last_seen'] = observed
                row['observations'] += 1
                row['memories'].append(p['memory'])
                if info['error']:
                    row['error'] = info['error']
                ukey = (uuid, user_key(row))
                total = totals.setdefault(ukey, dict(uid=row['uid'], username=row['username'], memory=0, known=True))
                if p['memory'] is None:
                    total['known'] = False
                else:
                    total['memory'] += p['memory']
            for key, total in totals.items():
                entry = user_memory.setdefault(key, dict(total, values=[]))
                entry['values'].append(total['memory'] if total['known'] else None)
    window['end'] = wall()
    timely = clock.monotonic() - origin <= config.duration + config.interval
    if not timely:
        window['errors'].append('sampling exceeded final monotonic deadline')
    window['complete'] = window['successful_samples'] == expected and window['actual_samples'] == expected and timely
    for uuid, readings in gpu_rows.items():
        last = readings[-1]
        row = {k: last[k] for k in ('uuid', 'index', 'name', 'memory_total', 'mig')}
        row.update(samples=len(readings), processes_ok=all(g['processes_ok'] for g in readings),
                   util_ok=all(g['util_ok'] for g in readings), capabilities=last['capabilities'],
                   errors=sorted(set(e for g in readings for e in g['errors'])))
        for field in ('gpu_util', 'memory_util', 'memory_used', 'power', 'temperature'):
            row[field] = stats([g[field] for g in readings])
        window['gpus'].append(row)
    gpu_map = {g['uuid']: g for g in window['gpus']}
    for (uuid, identity), row in proc_rows.items():
        seen = set()
        samples = []
        for s in util_rows[uuid]:
            stamp = s['timestamp'] / 1e6
            if (s['pid'] == row['pid'] and row['trusted'] and row['start_time'] <= stamp <= row['last_seen']
                    and started <= stamp <= window['end'] and s['timestamp'] not in seen):
                seen.add(s['timestamp'])
                samples.append(s)
        for field in ('sm', 'memory_util', 'encoder', 'decoder'):
            row[field] = stats([s[field] for s in samples])
        row['util_samples'] = len(samples)
        span = (max(seen) - min(seen)) / 1e6 if len(seen) >= 2 else 0
        bins = {min(expected - 1, int((s['timestamp'] / 1e6 - started) / config.interval)) for s in samples}
        row['util_coverage'] = min(1, span / config.duration, len(bins) / expected)
        # SM coverage excludes entries whose SM value is unavailable. Keep the
        # existing all-engine coverage and inactivity decision unchanged.
        sm_stamps = {s['timestamp'] for s in samples if s['sm'] is not None}
        sm_span = (max(sm_stamps) - min(sm_stamps)) / 1e6 if len(sm_stamps) >= 2 else 0
        sm_bins = {min(expected - 1, int((stamp / 1e6 - started) / config.interval)) for stamp in sm_stamps}
        row['sm_samples'] = len(sm_stamps)
        row['sm_coverage'] = min(1, sm_span / config.duration, len(sm_bins) / expected)
        row_memory = row.pop('memories')
        row['memory'] = stats(row_memory)
        gpu = gpu_map[uuid]
        row['valid_inactivity'] = bool(window['complete'] and gpu['samples'] == expected
            and gpu['processes_ok'] and gpu['util_ok'] and row['trusted'] and not row['error']
            and row['observations'] == expected and row['memory']['avg'] is not None and all(v is not None for v in row_memory)
            and row['util_coverage'] >= config.min_util_coverage
            and all(all(s[f] is not None for f in ('sm', 'memory_util', 'encoder', 'decoder')) for s in samples))
        engines = [row[f]['max'] for f in ('sm', 'memory_util', 'encoder', 'decoder') if row[f]['max'] is not None]
        if engines and max(engines) > 0:
            row['status'] = 'ACTIVE' if max(engines) >= config.active_threshold else 'LOW_ACTIVITY'
        elif row['valid_inactivity']:
            row['status'] = 'NO_ACTIVITY_OBSERVED'
        else:
            row['status'] = 'UNKNOWN'
        row['idle_streak'] = 0
        window['processes'].append(row)
    for (uuid, key), entry in user_memory.items():
        window['users'].append(dict(gpu_uuid=uuid, user_key=key, uid=entry['uid'],
            username=entry['username'], memory=stats(entry['values'])))
    return window
