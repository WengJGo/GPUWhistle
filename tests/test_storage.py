import pytest
from gpumonitor.storage import Storage, collection_lock


def test_persistence_and_retention(rig, config):
    clock, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
    with Storage(config.database) as db:
        assert db.latest()['complete']
        clock.sleep(31*86400)
        db.save(sample(), config)
        assert len(db.windows(0, clock.time())) == 1


def test_collection_lock_excludes_writer(config):
    with collection_lock(config.database):
        with pytest.raises(RuntimeError, match='already running'):
            with collection_lock(config.database):
                pass


def test_wal_reader_sees_new_window(rig, config):
    _, _, _, sample = rig
    with Storage(config.database) as reader, Storage(config.database) as writer:
        assert reader.latest() is None
        writer.save(sample(), config)
        assert reader.latest()['complete']


def test_retention_boundary_keeps_only_last_thirty_days(rig, config):
    from copy import deepcopy
    clock, _, _, sample = rig
    latest = sample()
    cutoff = latest['end'] - 30*86400
    with Storage(config.database) as db:
        for age in (1, 0, -1):
            window = deepcopy(latest)
            window['end'] = cutoff - age
            window['start'] = window['end'] - config.duration
            db.save(window, config)
        db.save(latest, config)
        ends = [row['end'] for row in db.windows(cutoff-10, clock.time())]
        assert cutoff-1 not in ends
        assert ends == [cutoff, cutoff+1, latest['end']]


def test_failed_collection_still_prunes_expired_history(rig, config):
    clock, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
        clock.sleep(31*86400)
        failed = dict(start=clock.time(), end=clock.time(), complete=False,
                      gpus=[], processes=[], users=[], errors=['synthetic failure'])
        db.save(failed, config)
        assert db.windows(0, clock.time()) == [failed]


@pytest.mark.parametrize('mask', [0, 0o022])
def test_new_database_and_wal_are_private_with_permissive_umask(config, mask):
    import os
    import stat
    from pathlib import Path
    path = Path(config.database)
    old_mask = os.umask(mask)
    try:
        with Storage(path) as db:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            for suffix in ('-wal', '-shm'):
                sidecar = Path(str(path) + suffix)
                assert sidecar.exists()
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        # The library must not alter the process-global umask.
        assert os.umask(mask) == mask
    finally:
        os.umask(old_mask)


def test_existing_database_permissions_and_data_are_preserved(rig, config):
    import stat
    from pathlib import Path
    _, _, _, sample = rig
    with Storage(config.database) as db:
        db.save(sample(), config)
        expected = db.latest()
    path = Path(config.database)
    path.chmod(0o640)
    with Storage(path) as db:
        assert db.latest() == expected
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_in_memory_database_does_not_create_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with Storage(':memory:') as db:
        assert db.latest() is None
    assert list(tmp_path.iterdir()) == []
