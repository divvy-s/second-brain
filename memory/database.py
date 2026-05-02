from __future__ import annotations

import json
import math
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent, ensure_aware, utc_now
from memory.backends import DatabaseBackend, SQLiteBackend

logger = logging.getLogger(__name__)


PERSON_ENTITY_LABELS = {"PERSON", "PER", "NORP"}
STRUCTURED_ENTITY_LABELS = {"ORG", "PRODUCT", "WORK_OF_ART", "EVENT", "GPE", "LOC", "FAC"}
IGNORED_PROPER_NOUNS = {
    "approval",
    "brain",
    "calendar",
    "launch",
    "message",
    "notify",
    "remind",
    "review",
    "schedule",
    "second",
    "send",
    "sync",
    "task",
    "weekly",
}


@dataclass(frozen=True)
class EventMemoryStats:
    event_id: str
    semantic_summary: str
    access_count: int
    retrieval_count: int
    planning_count: int
    execution_count: int
    reinforcement: float
    priority_score: float
    decay_factor: float
    effective_score: float
    priority_components: dict[str, Any]
    last_accessed_at: str | None


def _json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class _PgConnWrapper:
    """Thin wrapper so ``with db.connect() as conn:`` works for Postgres.

    * ``__enter__`` returns this wrapper (which proxies ``execute``).
    * ``__exit__`` commits on success, rolls-back on error, then closes.
    * ``execute(sql, params)`` transparently rewrites ``?`` → ``%s``.
    * Rows are returned as ``RealDictRow`` (dict-like) from psycopg2.
    """

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    # context-manager ---------------------------------------------------------
    def __enter__(self) -> "_PgConnWrapper":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()

    # query helpers -----------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> Any:
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self) -> Any:
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class MemoryDatabase:
    def __init__(self, path: str | Path, backend: DatabaseBackend | None = None) -> None:
        """Create the storage facade for structured memory and operational state.

        Direct `MemoryDatabase(path)` usage defaults to SQLite so tests, local tools,
        and one-off scripts do not unexpectedly connect to a configured remote
        PostgreSQL instance. Production wiring can still inject a backend explicitly.
        """
        self.path = Path(path)
        self.backend = backend or SQLiteBackend()
        # Only create parent dirs for SQLite file-based storage
        if self.backend.name == "sqlite":
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------------

    def _ph(self, count: int = 1) -> str:
        """Return comma-separated placeholders for the active backend."""
        p = self.backend.placeholder()
        return ", ".join(p for _ in range(count))

    def _phlist(self, items: list | tuple) -> str:
        """Return placeholders matching the length of *items*."""
        return self._ph(len(items))

    def _like_operator(self) -> str:
        return "LIKE" if self.backend.name == "sqlite" else "ILIKE"

    @contextmanager
    def _open_conn(self):
        """Yield a connection that commits on success / rolls back on error."""
        conn = self.backend.connect(self.path)
        self.backend.configure_connection(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def connect(self) -> Any:
        """Return a connection that works with ``with ... as conn:`` for both backends."""
        conn = self.backend.connect(self.path)
        self.backend.configure_connection(conn)
        if self.backend.name == "sqlite":
            return conn
        # Wrap psycopg2 connection so ``with self.connect() as conn:`` commits/rolls back
        return _PgConnWrapper(conn)

    def _execute(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        """Execute *sql* after rewriting ``?`` placeholders for the active backend."""
        if self.backend.name != "sqlite":
            sql = sql.replace("?", "%s")
        if self.backend.name == "sqlite":
            return conn.execute(sql, params)
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def _fetchone(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = self._execute(conn, sql, params)
        return cur.fetchone()

    def _fetchall(self, conn: Any, sql: str, params: tuple = ()) -> list:
        cur = self._execute(conn, sql, params)
        return cur.fetchall()

    def initialize(self) -> None:
        if self.backend.name == "sqlite":
            self._initialize_sqlite()
        else:
            self._initialize_postgres()

    # -- SQLite schema ---------------------------------------------------------

    def _initialize_sqlite(self) -> None:
        with self.connect() as conn:
            conn.executescript(self._sqlite_ddl())
            self._ensure_column_sqlite(conn, "context_events", "semantic_summary", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_sqlite(conn, "context_events", "access_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_sqlite(conn, "context_events", "retrieval_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_sqlite(conn, "context_events", "planning_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_sqlite(conn, "context_events", "execution_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_sqlite(conn, "context_events", "reinforcement", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column_sqlite(conn, "context_events", "priority_score", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column_sqlite(conn, "context_events", "decay_factor", "REAL NOT NULL DEFAULT 1.0")
            self._ensure_column_sqlite(conn, "context_events", "effective_score", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column_sqlite(conn, "context_events", "priority_components_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column_sqlite(conn, "context_events", "last_accessed_at", "TEXT")
            self._ensure_indexes_sqlite(conn)
            self._ensure_fts_tables(conn)

    @staticmethod
    def _sqlite_ddl() -> str:
        return """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                participants_json TEXT NOT NULL,
                importance REAL NOT NULL,
                entities_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                semantic_summary TEXT NOT NULL DEFAULT '',
                access_count INTEGER NOT NULL DEFAULT 0,
                retrieval_count INTEGER NOT NULL DEFAULT 0,
                planning_count INTEGER NOT NULL DEFAULT 0,
                execution_count INTEGER NOT NULL DEFAULT 0,
                reinforcement REAL NOT NULL DEFAULT 0.0,
                priority_score REAL NOT NULL DEFAULT 0.0,
                decay_factor REAL NOT NULL DEFAULT 1.0,
                effective_score REAL NOT NULL DEFAULT 0.0,
                priority_components_json TEXT NOT NULL DEFAULT '{}',
                last_accessed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(label, normalized_value)
            );
            CREATE TABLE IF NOT EXISTS entity_mentions (
                event_id TEXT NOT NULL REFERENCES context_events(id) ON DELETE CASCADE,
                entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                confidence REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY(event_id, entity_id)
            );
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                normalized_name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                normalized_email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                importance REAL NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persistent_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                context_text TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                importance REAL NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_type, normalized_name)
            );
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kg_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_node TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
                to_node TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(from_node, to_node, relation)
            );
            CREATE TABLE IF NOT EXISTS action_log (
                id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                plugin TEXT NOT NULL,
                status TEXT NOT NULL,
                risk TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT REFERENCES context_events(id) ON DELETE SET NULL,
                action_id TEXT REFERENCES action_log(id) ON DELETE SET NULL,
                rating INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approval_requests (
                id TEXT PRIMARY KEY,
                action_json TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rollback_actions (
                action_id TEXT PRIMARY KEY,
                undo_action_json TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings_store (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """

    def _ensure_column_sqlite(self, conn: Any, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_indexes_sqlite(self, conn: Any) -> None:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_context_events_occurred_at ON context_events(occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_context_events_effective_score ON context_events(effective_score DESC, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(normalized_name);
            CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(normalized_email);
            CREATE INDEX IF NOT EXISTS idx_persistent_entities_name ON persistent_entities(entity_type, normalized_name);
            """
        )

    # -- PostgreSQL schema -----------------------------------------------------

    def _initialize_postgres(self) -> None:
        with self._open_conn() as conn:
            cur = conn.cursor()
            for stmt in self._postgres_ddl():
                cur.execute(stmt)
            self._ensure_indexes_postgres(cur)
            cur.close()
        logger.info("PostgreSQL schema initialized")

    @staticmethod
    def _postgres_ddl() -> list[str]:
        return [
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS context_events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                participants_json TEXT NOT NULL,
                importance DOUBLE PRECISION NOT NULL,
                entities_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                semantic_summary TEXT NOT NULL DEFAULT '',
                access_count INTEGER NOT NULL DEFAULT 0,
                retrieval_count INTEGER NOT NULL DEFAULT 0,
                planning_count INTEGER NOT NULL DEFAULT 0,
                execution_count INTEGER NOT NULL DEFAULT 0,
                reinforcement DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                priority_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                decay_factor DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                effective_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                priority_components_json TEXT NOT NULL DEFAULT '{}',
                last_accessed_at TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS entities (
                id SERIAL PRIMARY KEY,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(label, normalized_value)
            )""",
            """CREATE TABLE IF NOT EXISTS entity_mentions (
                event_id TEXT NOT NULL REFERENCES context_events(id) ON DELETE CASCADE,
                entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                PRIMARY KEY(event_id, entity_id)
            )""",
            """CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                normalized_name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                normalized_email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS persistent_entities (
                id SERIAL PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                context_text TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_type, normalized_name)
            )""",
            """CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS kg_edges (
                id SERIAL PRIMARY KEY,
                from_node TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
                to_node TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(from_node, to_node, relation)
            )""",
            """CREATE TABLE IF NOT EXISTS action_log (
                id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                plugin TEXT NOT NULL,
                status TEXT NOT NULL,
                risk TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                event_id TEXT REFERENCES context_events(id) ON DELETE SET NULL,
                action_id TEXT REFERENCES action_log(id) ON DELETE SET NULL,
                rating INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS approval_requests (
                id TEXT PRIMARY KEY,
                action_json TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS rollback_actions (
                action_id TEXT PRIMARY KEY,
                undo_action_json TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS settings_store (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
        ]

    @staticmethod
    def _ensure_indexes_postgres(cur: Any) -> None:
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_context_events_occurred_at ON context_events(occurred_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_context_events_effective_score ON context_events(effective_score DESC, occurred_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(normalized_name)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(normalized_email)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_entities_name ON persistent_entities(entity_type, normalized_name)",
        ]:
            cur.execute(stmt)

    # -- FTS (SQLite only) -----------------------------------------------------

    def _ensure_fts_tables(self, conn: Any) -> None:
        if not self.backend.supports_fts():
            return
        self._ensure_fts_table(
            conn,
            table_name="context_events_fts",
            create_sql="""
                CREATE VIRTUAL TABLE context_events_fts
                USING fts5(event_id UNINDEXED, title, body, semantic_summary, source, kind)
            """,
            populate_sql="""
                INSERT INTO context_events_fts(event_id, title, body, semantic_summary, source, kind)
                SELECT id, title, body, semantic_summary, source, kind
                FROM context_events
            """,
            required_sql_fragments=("semantic_summary", "source", "kind"),
            source_table="context_events",
        )
        self._ensure_fts_table(
            conn,
            table_name="contacts_fts",
            create_sql="""
                CREATE VIRTUAL TABLE contacts_fts
                USING fts5(contact_id UNINDEXED, name, email, phone, metadata)
            """,
            populate_sql="""
                INSERT INTO contacts_fts(contact_id, name, email, phone, metadata)
                SELECT id, name, email, phone, metadata_json
                FROM contacts
            """,
            required_sql_fragments=("contact_id", "metadata"),
            source_table="contacts",
        )
        self._ensure_fts_table(
            conn,
            table_name="persistent_entities_fts",
            create_sql="""
                CREATE VIRTUAL TABLE persistent_entities_fts
                USING fts5(entity_id UNINDEXED, entity_type, name, context_text, attributes)
            """,
            populate_sql="""
                INSERT INTO persistent_entities_fts(entity_id, entity_type, name, context_text, attributes)
                SELECT id, entity_type, name, context_text, attributes_json
                FROM persistent_entities
            """,
            required_sql_fragments=("entity_id", "attributes"),
            source_table="persistent_entities",
        )

    def _ensure_fts_table(
        self,
        conn: Any,
        *,
        table_name: str,
        create_sql: str,
        populate_sql: str,
        required_sql_fragments: tuple[str, ...],
        source_table: str,
    ) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        existing_sql = str(row["sql"] or "").lower() if row else ""
        requires_rebuild = not existing_sql or any(fragment.lower() not in existing_sql for fragment in required_sql_fragments)
        if not requires_rebuild and row is not None:
            indexed_count = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
            source_count = int(conn.execute(f"SELECT COUNT(*) FROM {source_table}").fetchone()[0])
            requires_rebuild = source_count > 0 and indexed_count == 0
        if requires_rebuild:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.execute(create_sql)
            conn.execute(populate_sql)

    def upsert_user(self, user_id: str, display_name: str = "", timezone_name: str = "UTC") -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users(id, display_name, timezone, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    timezone = excluded.timezone
                """,
                (user_id, display_name, timezone_name, now),
            )

    def add_event(
        self,
        event: ContextEvent | dict[str, Any],
        *,
        semantic_summary: str | None = None,
    ) -> ContextEvent:
        context_event = event if isinstance(event, ContextEvent) else ContextEvent.from_dict(event)
        metadata = dict(context_event.metadata)
        summary = (semantic_summary or metadata.get("semantic_summary") or context_event.body or context_event.title).strip()
        metadata["semantic_summary"] = summary
        ranking = metadata.get("ranking", {}) if isinstance(metadata.get("ranking"), dict) else {}

        stored_event = ContextEvent(
            id=context_event.id,
            source=context_event.source,
            kind=context_event.kind,
            title=context_event.title,
            body=context_event.body,
            occurred_at=context_event.occurred_at,
            participants=list(context_event.participants),
            importance=context_event.importance,
            entities=list(context_event.entities),
            metadata=metadata,
        )

        now = utc_now().isoformat()
        occurred = ensure_aware(stored_event.occurred_at).astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO context_events(
                    id, source, kind, title, body, occurred_at, participants_json,
                    importance, entities_json, metadata_json, semantic_summary,
                    priority_score, decay_factor, effective_score, priority_components_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    kind = excluded.kind,
                    title = excluded.title,
                    body = excluded.body,
                    occurred_at = excluded.occurred_at,
                    participants_json = excluded.participants_json,
                    importance = excluded.importance,
                    entities_json = excluded.entities_json,
                    metadata_json = excluded.metadata_json,
                    semantic_summary = excluded.semantic_summary,
                    priority_components_json = excluded.priority_components_json
                """,
                (
                    stored_event.id,
                    stored_event.source,
                    stored_event.kind,
                    stored_event.title,
                    stored_event.body,
                    occurred,
                    _json_dump(stored_event.participants),
                    stored_event.importance,
                    _json_dump(stored_event.entities),
                    _json_dump(metadata),
                    summary,
                    float(ranking.get("priority_score", 0.0)),
                    float(ranking.get("decay_factor", 1.0)),
                    float(ranking.get("effective_score", 0.0)),
                    _json_dump(ranking.get("components", {})),
                    now,
                ),
            )
            if self.backend.supports_fts():
                conn.execute("DELETE FROM context_events_fts WHERE event_id = ?", (stored_event.id,))
                conn.execute(
                    """
                    INSERT INTO context_events_fts(event_id, title, body, semantic_summary, source, kind)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored_event.id,
                        stored_event.title,
                        stored_event.body,
                        summary,
                        stored_event.source,
                        stored_event.kind,
                    ),
                )
            for entity in stored_event.entities:
                entity_id = self._upsert_observed_entity(conn, entity)
                conn.execute(
                    """
                    INSERT INTO entity_mentions(event_id, entity_id, confidence)
                    VALUES (?, ?, ?)
                    ON CONFLICT(event_id, entity_id) DO UPDATE SET confidence = excluded.confidence
                    """,
                    (stored_event.id, entity_id, float(entity.get("confidence", 1.0))),
                )
            self._sync_structured_memory(conn, stored_event)
        return stored_event

    def _upsert_observed_entity(self, conn: Any, entity: dict[str, Any]) -> int:
        now = utc_now().isoformat()
        label = str(entity.get("label", "ENTITY"))
        value = str(entity.get("value", ""))
        normalized = str(entity.get("normalized_value") or value).lower()
        conn.execute(
            """
            INSERT INTO entities(label, value, normalized_value, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(label, normalized_value) DO UPDATE SET
                value = excluded.value,
                last_seen_at = excluded.last_seen_at
            """,
            (label, value, normalized, now, now),
        )
        row = conn.execute(
            "SELECT id FROM entities WHERE label = ? AND normalized_value = ?",
            (label, normalized),
        ).fetchone()
        return int(row["id"])

    def _sync_structured_memory(self, conn: Any, event: ContextEvent) -> None:
        for participant in event.participants:
            participant = participant.strip()
            if not participant:
                continue
            email = participant if "@" in participant else ""
            name = participant if "@" not in participant else participant.split("@", 1)[0]
            self._upsert_contact_conn(
                conn,
                name=name,
                email=email,
                phone="",
                metadata={"sources": [event.source], "auto_created": True},
                importance=max(event.importance, 0.55),
            )

        for entity in event.entities:
            label = str(entity.get("label", "ENTITY")).upper()
            value = str(entity.get("value", "")).strip()
            normalized = _normalize(str(entity.get("normalized_value") or value))
            if not value or not normalized:
                continue
            if label == "EMAIL":
                self._upsert_contact_conn(
                    conn,
                    name=value.split("@", 1)[0],
                    email=value,
                    phone="",
                    metadata={"sources": [event.source], "auto_created": True},
                    importance=max(event.importance, 0.6),
                )
            elif label in PERSON_ENTITY_LABELS:
                self._upsert_contact_conn(
                    conn,
                    name=value,
                    email="",
                    phone="",
                    metadata={"sources": [event.source], "auto_created": True},
                    importance=max(event.importance, 0.6),
                )
            elif label == "PROPER_NOUN" and normalized not in IGNORED_PROPER_NOUNS:
                self._upsert_entity_conn(
                    conn,
                    entity_type="person_or_topic",
                    name=value,
                    context_text=f"{event.title}\n{event.body}".strip(),
                    attributes={"source": event.source, "kind": event.kind, "auto_created": True},
                    importance=max(event.importance, 0.55),
                )
            elif label in STRUCTURED_ENTITY_LABELS:
                self._upsert_entity_conn(
                    conn,
                    entity_type=label.lower(),
                    name=value,
                    context_text=f"{event.title}\n{event.body}".strip(),
                    attributes={"source": event.source, "kind": event.kind, "auto_created": True},
                    importance=max(event.importance, 0.55),
                )

    def get_event(self, event_id: str) -> ContextEvent | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM context_events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def get_events_by_ids(self, event_ids: list[str]) -> dict[str, ContextEvent]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM context_events WHERE id IN ({placeholders})",
                tuple(event_ids),
            ).fetchall()
        return {str(row["id"]): self._row_to_event(row) for row in rows}

    def get_event_stats(self, event_id: str) -> EventMemoryStats:
        return self.get_event_stats_map([event_id]).get(
            event_id,
            EventMemoryStats(
                event_id=event_id,
                semantic_summary="",
                access_count=0,
                retrieval_count=0,
                planning_count=0,
                execution_count=0,
                reinforcement=0.0,
                priority_score=0.0,
                decay_factor=1.0,
                effective_score=0.0,
                priority_components={},
                last_accessed_at=None,
            ),
        )

    def get_event_stats_map(self, event_ids: list[str]) -> dict[str, EventMemoryStats]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, semantic_summary, access_count, retrieval_count, planning_count,
                       execution_count, reinforcement, priority_score, decay_factor,
                       effective_score, priority_components_json, last_accessed_at
                FROM context_events
                WHERE id IN ("""
                + placeholders
                + ")",
                tuple(event_ids),
            ).fetchall()
        stats: dict[str, EventMemoryStats] = {}
        for row in rows:
            stats[str(row["id"])] = EventMemoryStats(
                event_id=str(row["id"]),
                semantic_summary=str(row["semantic_summary"] or ""),
                access_count=int(row["access_count"] or 0),
                retrieval_count=int(row["retrieval_count"] or 0),
                planning_count=int(row["planning_count"] or 0),
                execution_count=int(row["execution_count"] or 0),
                reinforcement=float(row["reinforcement"] or 0.0),
                priority_score=float(row["priority_score"] or 0.0),
                decay_factor=float(row["decay_factor"] or 1.0),
                effective_score=float(row["effective_score"] or 0.0),
                priority_components=_json_load(row["priority_components_json"], {}),
                last_accessed_at=row["last_accessed_at"],
            )
        return stats

    def all_events(self, limit: int | None = None) -> list[ContextEvent]:
        sql = "SELECT * FROM context_events ORDER BY occurred_at DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def recent_events(self, limit: int = 50) -> list[ContextEvent]:
        return self.all_events(limit=limit)

    def ranked_events(self, limit: int = 50) -> list[ContextEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM context_events
                ORDER BY effective_score DESC, priority_score DESC, occurred_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def search_keyword(self, query: str, limit: int = 10) -> list[tuple[ContextEvent, float]]:
        terms = re.findall(r"[A-Za-z0-9_]+", query)
        if not terms:
            return []
        if self.backend.supports_fts():
            fts_query = " OR ".join(terms)
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT context_events.*, bm25(context_events_fts) AS rank
                    FROM context_events_fts
                    JOIN context_events ON context_events.id = context_events_fts.event_id
                    WHERE context_events_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            hits: list[tuple[ContextEvent, float]] = []
            for row in rows:
                rank = float(row["rank"])
                score = 1.0 / (1.0 + max(0.0, rank + 10.0))
                hits.append((self._row_to_event(row), score))
            return hits
        # Postgres fallback: ILIKE search
        needle = f"%{query.strip()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM context_events
                WHERE title ILIKE ? OR body ILIKE ? OR semantic_summary ILIKE ?
                ORDER BY effective_score DESC, occurred_at DESC
                LIMIT ?
                """,
                (needle, needle, needle, limit),
            ).fetchall()
        return [(self._row_to_event(row), 0.5) for row in rows]

    def update_event_ranking(
        self,
        event_id: str,
        *,
        priority_score: float,
        decay_factor: float,
        effective_score: float,
        reinforcement: float,
        priority_components: dict[str, Any] | None = None,
        semantic_summary: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE context_events
                SET priority_score = ?,
                    decay_factor = ?,
                    effective_score = ?,
                    reinforcement = ?,
                    priority_components_json = ?,
                    semantic_summary = COALESCE(?, semantic_summary)
                WHERE id = ?
                """,
                (
                    max(0.0, min(1.0, priority_score)),
                    max(0.0, min(1.0, decay_factor)),
                    max(0.0, min(1.0, effective_score)),
                    max(0.0, min(1.0, reinforcement)),
                    _json_dump(priority_components or {}),
                    semantic_summary,
                    event_id,
                ),
            )

    def increment_event_access(self, event_id: str, reason: str = "access", amount: int = 1) -> EventMemoryStats:
        amount = max(1, int(amount))
        column = {
            "retrieval": "retrieval_count",
            "planning": "planning_count",
            "execution": "execution_count",
        }.get(reason, None)
        now = utc_now().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT access_count, retrieval_count, planning_count, execution_count
                FROM context_events
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
            if row is None:
                return self.get_event_stats(event_id)
            access_count = int(row["access_count"] or 0) + amount
            retrieval_count = int(row["retrieval_count"] or 0) + (amount if column == "retrieval_count" else 0)
            planning_count = int(row["planning_count"] or 0) + (amount if column == "planning_count" else 0)
            execution_count = int(row["execution_count"] or 0) + (amount if column == "execution_count" else 0)
            reinforcement = self.calculate_reinforcement(
                access_count=access_count,
                retrieval_count=retrieval_count,
                planning_count=planning_count,
                execution_count=execution_count,
            )
            conn.execute(
                """
                UPDATE context_events
                SET access_count = ?,
                    retrieval_count = ?,
                    planning_count = ?,
                    execution_count = ?,
                    reinforcement = ?,
                    last_accessed_at = ?
                WHERE id = ?
                """,
                (
                    access_count,
                    retrieval_count,
                    planning_count,
                    execution_count,
                    reinforcement,
                    now,
                    event_id,
                ),
            )
        return self.get_event_stats(event_id)

    def calculate_reinforcement(
        self,
        *,
        access_count: int,
        retrieval_count: int,
        planning_count: int,
        execution_count: int,
    ) -> float:
        weighted_access = (
            access_count
            + (0.5 * retrieval_count)
            + (1.5 * planning_count)
            + (2.0 * execution_count)
        )
        return max(0.0, min(1.0, math.log1p(weighted_access) / 4.0))

    def upsert_contact(
        self,
        *,
        name: str,
        email: str = "",
        phone: str = "",
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            contact = self._upsert_contact_conn(conn, name, email, phone, metadata, importance)
        return contact

    def _upsert_contact_conn(
        self,
        conn: Any,
        name: str,
        email: str,
        phone: str,
        metadata: dict[str, Any] | None,
        importance: float,
    ) -> dict[str, Any]:
        now = utc_now().isoformat()
        normalized_name = _normalize(name)
        normalized_email = _normalize(email)
        existing = None
        if normalized_email:
            existing = conn.execute(
                "SELECT * FROM contacts WHERE normalized_email = ?",
                (normalized_email,),
            ).fetchone()
        if existing is None and normalized_name:
            existing = conn.execute(
                "SELECT * FROM contacts WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()

        merged_metadata = metadata or {}
        if existing is not None:
            existing_metadata = _json_load(existing["metadata_json"], {})
            merged_metadata = self._merge_metadata(existing_metadata, merged_metadata)
            conn.execute(
                """
                UPDATE contacts
                SET name = ?,
                    normalized_name = ?,
                    email = ?,
                    normalized_email = ?,
                    phone = ?,
                    metadata_json = ?,
                    importance = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name or existing["name"],
                    normalized_name or existing["normalized_name"],
                    email or existing["email"],
                    normalized_email or existing["normalized_email"],
                    phone or existing["phone"],
                    _json_dump(merged_metadata),
                    max(float(existing["importance"]), importance),
                    now,
                    existing["id"],
                ),
            )
            row_id = int(existing["id"])
        else:
            conn.execute(
                """
                INSERT INTO contacts(
                    name, normalized_name, email, normalized_email, phone,
                    metadata_json, importance, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    normalized_name,
                    email,
                    normalized_email,
                    phone,
                    _json_dump(merged_metadata),
                    max(0.0, min(1.0, importance)),
                    now,
                    now,
                ),
            )
            row_id = self.backend.last_insert_id(conn)
        if self.backend.supports_fts():
            conn.execute("DELETE FROM contacts_fts WHERE contact_id = ?", (row_id,))
            conn.execute(
                """
                INSERT INTO contacts_fts(contact_id, name, email, phone, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row_id, name, email, phone, _json_dump(merged_metadata)),
            )
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (row_id,)).fetchone()
        return self._row_to_contact(row)

    def get_contact(self, query: str) -> dict[str, Any] | None:
        needle = _normalize(query)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM contacts
                WHERE normalized_name = ? OR normalized_email = ? OR phone = ?
                ORDER BY importance DESC, updated_at DESC
                LIMIT 1
                """,
                (needle, needle, query.strip()),
            ).fetchone()
        return self._row_to_contact(row) if row else None

    def get_contact_by_name(self, name: str) -> dict[str, Any] | None:
        needle = _normalize(name)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM contacts
                WHERE normalized_name = ?
                ORDER BY importance DESC, updated_at DESC
                LIMIT 1
                """,
                (needle,),
            ).fetchone()
        if row is None and needle:
            like_op = self._like_operator()
            with self.connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT * FROM contacts
                    WHERE normalized_name {like_op} ?
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (f"%{needle}%",),
                ).fetchone()
        return self._row_to_contact(row) if row else None

    def search_contacts(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        needle = _normalize(query)
        terms = re.findall(r"[A-Za-z0-9_@.+-]+", query)
        rows: list[Any] = []
        like_op = self._like_operator()
        with self.connect() as conn:
            if terms and self.backend.supports_fts():
                try:
                    fts_query = " OR ".join(terms)
                    rows = conn.execute(
                        """
                        SELECT contacts.*
                        FROM contacts_fts
                        JOIN contacts ON contacts.id = contacts_fts.contact_id
                        WHERE contacts_fts MATCH ?
                        ORDER BY contacts.importance DESC, contacts.updated_at DESC
                        LIMIT ?
                        """,
                        (fts_query, limit),
                    ).fetchall()
                except Exception:
                    rows = []
            if not rows:
                rows = conn.execute(
                    f"""
                    SELECT * FROM contacts
                    WHERE normalized_name {like_op} ? OR normalized_email {like_op} ?
                       OR phone {like_op} ? OR metadata_json {like_op} ?
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (f"%{needle}%", f"%{needle}%", f"%{query.strip()}%", f"%{query.strip()}%", limit),
                ).fetchall()
        return [self._row_to_contact(row) for row in rows]

    def set_preference(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO preferences(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, _json_dump(value), utc_now().isoformat()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM preferences WHERE key = ?", (key,)).fetchone()
        return _json_load(row["value_json"], default) if row else default

    def matching_preferences(self, query: str) -> list[dict[str, Any]]:
        tokens = {_normalize(token) for token in re.findall(r"[A-Za-z0-9_]+", query)}
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT key, value_json, updated_at
                FROM preferences
                WHERE lower(key) IN ({placeholders})
                   OR {" OR ".join("lower(key) LIKE ?" for _ in tokens)}
                ORDER BY updated_at DESC
                """,
                tuple(tokens) + tuple(f"%{token}%" for token in tokens),
            ).fetchall()
        return [
            {"key": str(row["key"]), "value": _json_load(row["value_json"], None), "updated_at": row["updated_at"]}
            for row in rows
        ]

    def upsert_entity_record(
        self,
        *,
        entity_type: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        context_text: str = "",
        importance: float = 0.5,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            return self._upsert_entity_conn(conn, entity_type, name, context_text, attributes, importance)

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        context_text: str = "",
        importance: float = 0.5,
    ) -> dict[str, Any]:
        return self.upsert_entity_record(
            entity_type=entity_type,
            name=name,
            attributes=attributes,
            context_text=context_text,
            importance=importance,
        )

    def _upsert_entity_conn(
        self,
        conn: Any,
        entity_type: str,
        name: str,
        context_text: str,
        attributes: dict[str, Any] | None,
        importance: float,
    ) -> dict[str, Any]:
        now = utc_now().isoformat()
        normalized_name = _normalize(name)
        existing = conn.execute(
            """
            SELECT * FROM persistent_entities
            WHERE entity_type = ? AND normalized_name = ?
            """,
            (entity_type, normalized_name),
        ).fetchone()
        merged_attributes = attributes or {}
        if existing is not None:
            merged_attributes = self._merge_metadata(_json_load(existing["attributes_json"], {}), merged_attributes)
            conn.execute(
                """
                UPDATE persistent_entities
                SET name = ?,
                    context_text = CASE
                        WHEN ? <> '' THEN ?
                        ELSE context_text
                    END,
                    attributes_json = ?,
                    importance = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    context_text,
                    context_text,
                    _json_dump(merged_attributes),
                    max(float(existing["importance"]), importance),
                    now,
                    existing["id"],
                ),
            )
            row_id = int(existing["id"])
        else:
            conn.execute(
                """
                INSERT INTO persistent_entities(
                    entity_type, name, normalized_name, context_text, attributes_json,
                    importance, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_type,
                    name,
                    normalized_name,
                    context_text,
                    _json_dump(merged_attributes),
                    max(0.0, min(1.0, importance)),
                    now,
                    now,
                ),
            )
            row_id = self.backend.last_insert_id(conn)
        if self.backend.supports_fts():
            conn.execute("DELETE FROM persistent_entities_fts WHERE entity_id = ?", (row_id,))
            conn.execute(
                """
                INSERT INTO persistent_entities_fts(entity_id, entity_type, name, context_text, attributes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row_id, entity_type, name, context_text, _json_dump(merged_attributes)),
            )
        row = conn.execute("SELECT * FROM persistent_entities WHERE id = ?", (row_id,)).fetchone()
        return self._row_to_persistent_entity(row)

    def search_entities(self, context: str, entity_type: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        terms = re.findall(r"[A-Za-z0-9_@.+-]+", context)
        params: list[Any] = []
        like_op = self._like_operator()
        sql = """
            SELECT persistent_entities.*
            FROM persistent_entities
            WHERE 1 = 1
        """
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        if terms:
            likes = " OR ".join(
                [
                    f"normalized_name {like_op} ?",
                    f"context_text {like_op} ?",
                    f"attributes_json {like_op} ?",
                ]
            )
            sql += f" AND ({likes})"
            needle = f"%{_normalize(context)}%"
            params.extend([needle, f"%{context.strip()}%", f"%{context.strip()}%"])
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_persistent_entity(row) for row in rows]

    def upsert_node(self, node_id: str, node_type: str, label: str, metadata: dict[str, Any] | None = None) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO kg_nodes(id, node_type, label, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    node_type = excluded.node_type,
                    label = excluded.label,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (node_id, node_type, label, _json_dump(metadata or {}), now),
            )

    def upsert_edge(
        self,
        from_node: str,
        to_node: str,
        relation: str,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO kg_edges(from_node, to_node, relation, weight, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(from_node, to_node, relation) DO UPDATE SET
                    weight = kg_edges.weight + excluded.weight,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (from_node, to_node, relation, weight, _json_dump(metadata or {}), now),
            )

    def log_action(
        self,
        action_id: str,
        action_type: str,
        plugin: str,
        status: str,
        risk: str,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO action_log(id, action_type, plugin, status, risk, request_json, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json
                """,
                (action_id, action_type, plugin, status, risk, _json_dump(request), _json_dump(result), now),
            )

    def list_action_logs(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        """Return recent action executions for audits and operator review."""
        sql = "SELECT * FROM action_log"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "id": str(row["id"]),
                "action_type": str(row["action_type"]),
                "plugin": str(row["plugin"]),
                "status": str(row["status"]),
                "risk": str(row["risk"]),
                "request": _json_load(row["request_json"], {}),
                "result": _json_load(row["result_json"], {}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def record_feedback(
        self,
        *,
        event_id: str | None,
        action_id: str | None,
        rating: int,
        note: str = "",
    ) -> None:
        """Persist explicit or implicit feedback for reinforcement analytics."""
        with self.connect() as conn:
            safe_event_id = event_id
            if event_id:
                event_exists = conn.execute(
                    "SELECT 1 FROM context_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                if event_exists is None:
                    safe_event_id = None
            safe_action_id = action_id
            if action_id:
                exists = conn.execute(
                    "SELECT 1 FROM action_log WHERE id = ?",
                    (action_id,),
                ).fetchone()
                if exists is None:
                    safe_action_id = None
            conn.execute(
                """
                INSERT INTO feedback(event_id, action_id, rating, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (safe_event_id, safe_action_id, int(rating), note, utc_now().isoformat()),
            )

    def adjust_event_importance(self, event_id: str, delta: float) -> ContextEvent | None:
        """Nudge stored event importance after accept/ignore feedback."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT importance FROM context_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            importance = max(0.0, min(1.0, float(row["importance"] or 0.0) + float(delta)))
            conn.execute(
                "UPDATE context_events SET importance = ? WHERE id = ?",
                (importance, event_id),
            )
        return self.get_event(event_id)

    def adjust_event_reinforcement(self, event_id: str, delta: float) -> EventMemoryStats:
        """Apply a bounded reinforcement adjustment after operator feedback."""
        stats = self.get_event_stats(event_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE context_events
                SET reinforcement = ?
                WHERE id = ?
                """,
                (max(0.0, min(1.0, stats.reinforcement + float(delta))), event_id),
            )
        return self.get_event_stats(event_id)

    def save_approval_request(
        self,
        request_id: str,
        action: dict[str, Any],
        risk: str,
        status: str,
        created_at: str,
        decided_at: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_requests(id, action_json, risk, status, created_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    action_json = excluded.action_json,
                    risk = excluded.risk,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    decided_at = excluded.decided_at
                """,
                (request_id, _json_dump(action), risk, status, created_at, decided_at),
            )

    def get_approval_request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "action": _json_load(row["action_json"], {}),
            "risk": str(row["risk"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "decided_at": row["decided_at"],
        }

    def list_approval_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM approval_requests"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": str(row["id"]),
                "action": _json_load(row["action_json"], {}),
                "risk": str(row["risk"]),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "decided_at": row["decided_at"],
            }
            for row in rows
        ]

    def save_rollback_action(self, action_id: str, undo_action: dict[str, Any], used: bool = False) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rollback_actions(action_id, undo_action_json, used, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(action_id) DO UPDATE SET
                    undo_action_json = excluded.undo_action_json,
                    used = excluded.used,
                    updated_at = excluded.updated_at
                """,
                (action_id, _json_dump(undo_action), 1 if used else 0, now, now),
            )

    def get_rollback_action(self, action_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT undo_action_json, used FROM rollback_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        if row is None:
            return None
        action = _json_load(row["undo_action_json"], {})
        action["used"] = bool(row["used"])
        return action

    def mark_rollback_action_used(self, action_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE rollback_actions
                SET used = 1, updated_at = ?
                WHERE action_id = ?
                """,
                (utc_now().isoformat(), action_id),
            )

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings_store(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, _json_dump(value), utc_now().isoformat()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM settings_store WHERE key = ?", (key,)).fetchone()
        return _json_load(row["value_json"], default) if row else default

    def _row_to_contact(self, row: Any | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "email": str(row["email"] or ""),
            "phone": str(row["phone"] or ""),
            "importance": float(row["importance"] or 0.0),
            "metadata": _json_load(row["metadata_json"], {}),
            "updated_at": row["updated_at"],
        }

    def _row_to_persistent_entity(self, row: Any | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": int(row["id"]),
            "type": str(row["entity_type"]),
            "name": str(row["name"]),
            "context": str(row["context_text"] or ""),
            "importance": float(row["importance"] or 0.0),
            "attributes": _json_load(row["attributes_json"], {}),
            "updated_at": row["updated_at"],
        }

    def _row_to_event(self, row: Any) -> ContextEvent:
        metadata = _json_load(row["metadata_json"], {})
        semantic_summary = str(row["semantic_summary"] or "")
        if semantic_summary:
            metadata.setdefault("semantic_summary", semantic_summary)
        metadata["ranking"] = {
            "priority_score": float(row["priority_score"] or 0.0),
            "decay_factor": float(row["decay_factor"] or 1.0),
            "effective_score": float(row["effective_score"] or 0.0),
            "reinforcement": float(row["reinforcement"] or 0.0),
            "access_count": int(row["access_count"] or 0),
            "retrieval_count": int(row["retrieval_count"] or 0),
            "planning_count": int(row["planning_count"] or 0),
            "execution_count": int(row["execution_count"] or 0),
            "components": _json_load(row["priority_components_json"], {}),
            "last_accessed_at": row["last_accessed_at"],
        }
        return ContextEvent(
            id=row["id"],
            source=row["source"],
            kind=row["kind"],
            title=row["title"],
            body=row["body"],
            occurred_at=row["occurred_at"],
            participants=_json_load(row["participants_json"], []),
            importance=float(row["importance"]),
            entities=_json_load(row["entities_json"], []),
            metadata=metadata,
        )

    def _merge_metadata(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged = dict(left)
        for key, value in right.items():
            if key not in merged:
                merged[key] = value
                continue
            if isinstance(merged[key], list) and isinstance(value, list):
                seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in merged[key]}
                for item in value:
                    marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if marker not in seen:
                        seen.add(marker)
                        merged[key].append(item)
                continue
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._merge_metadata(merged[key], value)
                continue
            if merged[key] in ("", None, [], {}):
                merged[key] = value
        return merged
