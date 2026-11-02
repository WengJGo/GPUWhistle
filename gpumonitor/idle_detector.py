def apply(window, previous, config):
    """Only the immediately preceding window may extend continuity."""
    prior = {(p['gpu_uuid'], p['identity']): p for p in previous.get('processes', [])} if previous else {}
    contiguous = previous and 0 <= window['start'] - previous['start'] <= config.period * 1.5
    for p in window['processes']:
        eligible = (p['status'] == 'NO_ACTIVITY_OBSERVED' and p['valid_inactivity']
                    and p['memory']['avg'] is not None and p['memory']['min'] >= config.idle_memory_bytes)
        old = prior.get((p['gpu_uuid'], p['identity'])) if contiguous else None
        p['idle_streak'] = (old.get('idle_streak', 0) if old else 0) + 1 if eligible else 0
        if p['idle_streak'] >= config.idle_windows:
            p['status'] = 'SUSPECTED_IDLE'
    return window
