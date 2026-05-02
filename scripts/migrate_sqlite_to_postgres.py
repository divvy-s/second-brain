from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()

from connectors.config import load_config
from memory.backends import create_backend
from memory.database import MemoryDatabase


TABLE_ORDER = [
    "users",
    "context_events",
    "entities",
    "entity_mentions",
    "contacts",
    "preferences",
    "persistent_entities",
    "kg_nodes",
    "kg_edges",
    "action_log",
    "feedback",
    "approval_requests",
    "rollback_actions",
    "settings_store",
]

SERIAL_ID_TABLES = [
    "entities",
    "contacts",
    "persistent_entities",
    "kg_edges",
    "feedback",
]


def _describe_postgres_target(database_url: str) -> str:
    parsed = urlsplit(database_url.strip())
    host = parsed.hostname or "configured-host"
    database_name = parsed.path.lstrip("/") or "configured-database"
    return f"{host}/{database_name}"


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def _table_row_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"]) if row else 0


def _postgres_row_count(conn: object, table_name: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS count FROM {table_name}")
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row["count"])
    return int(row[0])


def _assert_empty_target(conn: object) -> None:
    non_empty_tables: dict[str, int] = {}
    for table_name in TABLE_ORDER:
        row_count = _postgres_row_count(conn, table_name)
        if row_count > 0:
            non_empty_tables[table_name] = row_count
    if non_empty_tables:
        details = ", ".join(f"{name}={count}" for name, count in sorted(non_empty_tables.items()))
        raise RuntimeError(
            "PostgreSQL target is not empty. Start with an empty database before migrating "
            f"SQLite data. Non-empty tables: {details}"
        )


def _copy_table(sqlite_conn: sqlite3.Connection, postgres_conn: object, table_name: str) -> int:
    if not _sqlite_table_exists(sqlite_conn, table_name):
        return 0
    columns = _sqlite_columns(sqlite_conn, table_name)
    if not columns:
        return 0
    rows = sqlite_conn.execute(f"SELECT {', '.join(columns)} FROM {table_name}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
    values = [tuple(row[column] for column in columns) for row in rows]
    cur = postgres_conn.cursor()
    try:
        cur.executemany(sql, values)
    finally:
        cur.close()
    return len(values)


def _reset_sequences(conn: object) -> None:
    cur = conn.cursor()
    try:
        for table_name in SERIAL_ID_TABLES:
            cur.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                    EXISTS(SELECT 1 FROM {table_name})
                )
                """
            )
    finally:
        cur.close()


def migrate_sqlite_to_postgres(sqlite_path: Path, database_url: str) -> dict[str, int]:
    backend = create_backend(database_url)
    if backend.name != "postgres":
        raise RuntimeError("DATABASE_URL must point to a PostgreSQL database for migration")
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    MemoryDatabase(sqlite_path, backend=backend).initialize()
    sqlite_conn = _sqlite_connect(sqlite_path)
    postgres_conn = backend.connect(sqlite_path)
    backend.configure_connection(postgres_conn)
    try:
        _assert_empty_target(postgres_conn)
        copied_counts = {
            table_name: _copy_table(sqlite_conn, postgres_conn, table_name)
            for table_name in TABLE_ORDER
        }
        _reset_sequences(postgres_conn)
        postgres_conn.commit()
        return copied_counts
    except Exception:
        postgres_conn.rollback()
        raise
    finally:
        sqlite_conn.close()
        postgres_conn.close()


def main() -> int:
    import os

    root = _ROOT
    config = load_config(root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    sqlite_path = root / memory_config.get("sqlite_path", "data/second_brain.sqlite3")
    database_url = os.getenv("DATABASE_URL", memory_config.get("database_url", ""))
    target_label = _describe_postgres_target(database_url)

    copied_counts = migrate_sqlite_to_postgres(sqlite_path, database_url)
    copied_tables = [f"{name}={count}" for name, count in copied_counts.items() if count]
    copied_summary = ", ".join(copied_tables) if copied_tables else "no rows copied"
    print(f"Migrated SQLite data from {sqlite_path} to PostgreSQL ({target_label})")
    print(copied_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
