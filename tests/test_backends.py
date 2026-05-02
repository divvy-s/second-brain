from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from memory.backends import PostgresBackend, create_backend
from memory.database import MemoryDatabase, _PgConnWrapper


class FakeCursor:
    def __init__(self, *, fetchone_result: Any = None, fetchall_result: list[Any] | None = None) -> None:
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []
        self.sql = ""
        self.params: tuple[Any, ...] = ()
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.sql = sql
        self.params = params

    def fetchone(self) -> Any:
        return self.fetchone_result

    def fetchall(self) -> list[Any]:
        return self.fetchall_result

    def close(self) -> None:
        self.closed = True


class FakeRawPostgresConnection:
    def __init__(self, cursors: list[FakeCursor]) -> None:
        self._cursors = list(cursors)
        self.issued_cursors: list[FakeCursor] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        cursor = self._cursors.pop(0) if self._cursors else FakeCursor()
        self.issued_cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakePostgresBackend:
    name = "postgres"

    def __init__(self, raw_conn: FakeRawPostgresConnection) -> None:
        self.raw_conn = raw_conn

    def connect(self, path: Path) -> FakeRawPostgresConnection:
        return self.raw_conn

    def configure_connection(self, conn: FakeRawPostgresConnection) -> None:
        return None

    def last_insert_id(self, conn: Any) -> int:
        return 0

    def placeholder(self) -> str:
        return "%s"

    def supports_fts(self) -> bool:
        return False


class BackendTests(unittest.TestCase):
    def test_create_backend_uses_postgres_for_postgres_url(self) -> None:
        self.assertEqual(create_backend("postgresql://user:pass@localhost/db").name, "postgres")
        self.assertEqual(create_backend("data/second_brain.sqlite3").name, "sqlite")

    def test_pg_wrapper_rewrites_qmark_placeholders(self) -> None:
        raw_conn = FakeRawPostgresConnection([FakeCursor()])
        wrapper = _PgConnWrapper(raw_conn)
        cursor = wrapper.execute("SELECT * FROM contacts WHERE id = ? AND name = ?", (7, "devon"))
        self.assertEqual(cursor.sql, "SELECT * FROM contacts WHERE id = %s AND name = %s")
        self.assertEqual(cursor.params, (7, "devon"))

    def test_postgres_last_insert_id_accepts_wrapper_connections(self) -> None:
        backend = PostgresBackend("postgresql://example")
        raw_conn = FakeRawPostgresConnection([FakeCursor(fetchone_result={"lastval": 12})])
        wrapper = _PgConnWrapper(raw_conn)
        self.assertEqual(backend.last_insert_id(wrapper), 12)

    def test_search_entities_uses_ilike_for_postgres(self) -> None:
        raw_conn = FakeRawPostgresConnection([FakeCursor(fetchall_result=[])])
        database = MemoryDatabase(Path("ignored.sqlite3"), backend=FakePostgresBackend(raw_conn))
        database.search_entities("Launch Team")
        self.assertIn("ILIKE", raw_conn.issued_cursors[0].sql)


if __name__ == "__main__":
    unittest.main()
