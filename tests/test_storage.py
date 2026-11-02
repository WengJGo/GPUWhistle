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
