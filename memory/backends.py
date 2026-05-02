from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Protocol


class DatabaseBackend(Protocol):
    name: str

    def connect(self, path: Path) -> Any: ...

    def configure_connection(self, conn: Any) -> None: ...

    def last_insert_id(self, conn: Any) -> int: ...

    def placeholder(self) -> str:
        """Return the parameter placeholder style: '?' for SQLite, '%s' for Postgres."""
        ...

    def supports_fts(self) -> bool:
        """Whether this backend supports FTS5 virtual tables."""
        ...


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

    def placeholder(self) -> str:
        return "?"

    def supports_fts(self) -> bool:
        return True


class PostgresBackend:
    """PostgreSQL backend using psycopg2.

    The *path* argument is ignored; the connection string is read from
    ``DATABASE_URL`` env-var or from the ``dsn`` constructor parameter.
    """

    name = "postgres"

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv("DATABASE_URL", "")

    # --- lazy import so psycopg2 is only required when this backend is used ---
    @staticmethod
    def _pg():  # noqa: ANN205
        import psycopg2
        import psycopg2.extras
        return psycopg2, psycopg2.extras

    def connect(self, path: Path) -> Any:
        psycopg2, extras = self._pg()
        conn = psycopg2.connect(self._dsn, cursor_factory=extras.RealDictCursor)
        conn.autocommit = False
        return conn

    def configure_connection(self, conn: Any) -> None:
        # The connection is created with a RealDict cursor factory already.
        return None

    def last_insert_id(self, conn: Any) -> int:
        # In PostgreSQL we use RETURNING in the SQL itself; this is a fallback.
        cur = conn.cursor()
        cur.execute("SELECT lastval()")
        row = cur.fetchone()
        cur.close()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(list(row.values())[0])
        return int(row[0]) if row else 0

    def placeholder(self) -> str:
        return "%s"

    def supports_fts(self) -> bool:
        return False


def create_backend(database_url: str | None = None) -> DatabaseBackend:
    """Factory: return a PostgresBackend when *database_url* starts with
    ``postgres``, otherwise fall back to SQLiteBackend."""
    url = (database_url or os.getenv("DATABASE_URL", "")).strip()
    if url.startswith("postgres"):
        return PostgresBackend(dsn=url)  # type: ignore[return-value]
    return SQLiteBackend()  # type: ignore[return-value]
