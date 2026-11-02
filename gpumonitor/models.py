from statistics import mean

STATUSES = ('ACTIVE', 'LOW_ACTIVITY', 'NO_ACTIVITY_OBSERVED', 'SUSPECTED_IDLE', 'UNKNOWN')


def stats(values):
    values = [v for v in values if v is not None]
    return {'avg': mean(values) if values else None, 'max': max(values) if values else None, 'min': min(values) if values else None}


def user_key(process):
    # Unknown identities must never merge into a fabricated Linux user.
    return str(process['uid']) if process['uid'] is not None else 'unknown:' + process['identity']
