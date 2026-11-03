import json
import sqlite3
import threading
import time
from pathlib import Path

TABLES = ("jobs", "uploads")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        for table in TABLES:
            self._db.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL)"
            )

    def put(self, table: str, doc: dict) -> None:
        if table not in TABLES:
            raise ValueError(table)
        payload = json.dumps(doc, ensure_ascii=False)
        now = time.time()
        with self._lock:
            self._db.execute(
                f"INSERT INTO {table} (id, created, updated, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated = excluded.updated, data = excluded.data",
                (doc["id"], now, now, payload),
            )

    def all(self, table: str) -> list[dict]:
        if table not in TABLES:
            raise ValueError(table)
        with self._lock:
            rows = self._db.execute(f"SELECT data FROM {table} ORDER BY created").fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, table: str, key: str) -> None:
        if table not in TABLES:
            raise ValueError(table)
        with self._lock:
            self._db.execute(f"DELETE FROM {table} WHERE id = ?", (key,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
