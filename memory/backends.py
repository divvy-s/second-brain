from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol


class DatabaseBackend(Protocol):
    name: str

    def connect(self, path: Path) -> sqlite3.Connection: ...

    def configure_connection(self, conn: sqlite3.Connection) -> None: ...

    def last_insert_id(self, conn: sqlite3.Connection) -> int: ...


class SQLiteBackend:
    name = "sqlite"

    def connect(self, path: Path) -> sqlite3.Connection:
        return sqlite3.connect(path)

    def configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

    def last_insert_id(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return int(row[0]) if row else 0
