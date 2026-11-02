from gpumonitor.storage import Storage


def test_three_valid_windows(rig, config):
    clock, _, _, sample = rig
    with Storage(config.database) as db:
        for i in range(3):
            w = sample()
            db.save(w, config)
            clock.sleep(596)
        p = db.latest()['processes'][0]
        assert p['status'] == 'SUSPECTED_IDLE' and p['idle_streak'] == 3


def test_unknown_breaks_streak(rig, config):
    clock, _, backend, sample = rig
    with Storage(config.database) as db:
        for unknown in (False, False, True, False):
            backend.no_samples = unknown
            w = sample()
            db.save(w, config)
            clock.sleep(596)
        assert db.latest()['processes'][0]['idle_streak'] == 1


def test_identity_and_gap_reset(rig, config):
    clock, reader, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
        clock.sleep(596)
        reader.version[10] = 1
        db.save(sample(), config)
        assert db.latest()['processes'][0]['idle_streak'] == 1
        clock.sleep(2000)
        db.save(sample(), config)
        assert db.latest()['processes'][0]['idle_streak'] == 1


def test_memory_dips_below_threshold_not_idle(rig, config):
    _, _, backend, sample = rig
    original = backend.snapshot
    def snapshot(since):
        gpus, errors = original(since)
        for g in gpus:
            for p in g['processes']:
                p['memory'] = 2**29 if backend.steps == 2 else 2 * 2**30
        return gpus, errors
    backend.snapshot = snapshot
    with Storage(config.database) as db:
        db.save(sample(), config)
        p = db.latest()['processes'][0]
        assert p['status'] == 'NO_ACTIVITY_OBSERVED' and p['idle_streak'] == 0
