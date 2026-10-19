import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS transfers (
                    id TEXT PRIMARY KEY, object_key TEXT NOT NULL,
                    expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
                    event TEXT NOT NULL, types TEXT NOT NULL, source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_limits (
                    identity TEXT PRIMARY KEY, window REAL NOT NULL, count INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def event(conn, event: str, types: list[str], source: str):
        conn.execute(
            "INSERT INTO events(time,event,types,source) VALUES(?,?,?,?)",
            (now().isoformat(), event, json.dumps(sorted(set(types))), source),
        )

    def log(self, event: str, types: list[str], source: str):
        with self.connect() as conn:
            self.event(conn, event, types, source)

    def logs(self, limit: int):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT time,event,types,source FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "types": json.loads(row["types"])} for row in rows]

    def transfer(self, identifier: str):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM transfers WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def save_transfer(self, identifier, key, expires_at, types, source):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO transfers(id,object_key,expires_at) VALUES(?,?,?)",
                (identifier, key, expires_at),
            )
            self.event(conn, "TRANSFER", types, source)

    def revoke(self, identifier, source):
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE transfers SET revoked=1 WHERE id=? AND revoked=0", (identifier,)
            )
            if cursor.rowcount:
                self.event(conn, "REVOKE", [], source)

    def allow_plugin(self, identity: str, timestamp: float) -> bool:
        # SQLite serializes this transaction across threads and worker processes.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT window,count FROM rate_limits WHERE identity=?", (identity,)
            ).fetchone()
            if row is None or timestamp - row["window"] >= 60:
                conn.execute(
                    "INSERT OR REPLACE INTO rate_limits VALUES(?,?,1)", (identity, timestamp)
                )
                return True
            if row["count"] >= 30:
                return False
            conn.execute("UPDATE rate_limits SET count=count+1 WHERE identity=?", (identity,))
            return True
