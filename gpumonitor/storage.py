import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .idle_detector import apply


@contextmanager
def collection_lock(database):
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(str(path) + '.lock', 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another gpumonitor collector is already running')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Storage:
    def __init__(self, database):
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Reports and diagnostics can create the database before a collector
        # sets its umask. Create new files privately without changing the
        # caller's umask or permissions on an existing database.
        if str(path) != ':memory:':
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS windows(
            id INTEGER PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL,
            complete INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS windows_end ON windows(end);
        CREATE INDEX IF NOT EXISTS windows_start ON windows(start);
        PRAGMA user_version=1;
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def latest(self):
        row = self.db.execute('SELECT payload FROM windows ORDER BY start DESC, id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self, window, config):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            apply(window, self.latest(), config)
            self.db.execute('INSERT INTO windows(start,end,complete,payload) VALUES(?,?,?,?)',
                            (window['start'], window['end'], int(window['complete']), json.dumps(window)))
            self.db.execute('DELETE FROM windows WHERE end < ?', (window['end'] - config.retention_days * 86400,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # Deleted pages are reused; reclaim a bounded amount without full VACUUM.
        self.db.execute('PRAGMA incremental_vacuum(64)')

    def windows(self, since, until):
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT payload FROM windows WHERE end >= ? AND end <= ? ORDER BY start,id', (since, until))]
