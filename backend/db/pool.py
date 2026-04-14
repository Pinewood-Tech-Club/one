"""
Thread-local SQLite connection cache.

One connection is created per (thread, database-path) pair and reused for the
lifetime of that thread — eliminating the open/close overhead on every request
and reducing lock contention when combined with WAL journal mode (set in init_db).
"""
import sqlite3
import threading

_local = threading.local()


def get_conn(path: str) -> sqlite3.Connection:
    """Return the thread-local connection for *path*, creating it if needed.

    Callers should commit or roll back transactions but should not close the
    returned connection directly.
    """
    if not hasattr(_local, "conns"):
        _local.conns: dict[str, sqlite3.Connection] = {}
    if path not in _local.conns:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conns[path] = conn
    return _local.conns[path]
