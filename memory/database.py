from __future__ import annotations

import json
import re
import sqlite3
from datetime import timezone
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent, ensure_aware, utc_now


def _json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class MemoryDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
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
                    created_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS context_events_fts
                USING fts5(event_id UNINDEXED, title, body, source, kind);

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

                CREATE TABLE IF NOT EXISTS settings_store (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

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

    def add_event(self, event: ContextEvent | dict[str, Any]) -> ContextEvent:
        context_event = event if isinstance(event, ContextEvent) else ContextEvent.from_dict(event)
        now = utc_now().isoformat()
        occurred = ensure_aware(context_event.occurred_at).astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO context_events(
                    id, source, kind, title, body, occurred_at, participants_json,
                    importance, entities_json, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    kind = excluded.kind,
                    title = excluded.title,
                    body = excluded.body,
                    occurred_at = excluded.occurred_at,
                    participants_json = excluded.participants_json,
                    importance = excluded.importance,
                    entities_json = excluded.entities_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    context_event.id,
                    context_event.source,
                    context_event.kind,
                    context_event.title,
                    context_event.body,
                    occurred,
                    _json_dump(context_event.participants),
                    context_event.importance,
                    _json_dump(context_event.entities),
                    _json_dump(context_event.metadata),
                    now,
                ),
            )
            conn.execute("DELETE FROM context_events_fts WHERE event_id = ?", (context_event.id,))
            conn.execute(
                "INSERT INTO context_events_fts(event_id, title, body, source, kind) VALUES (?, ?, ?, ?, ?)",
                (
                    context_event.id,
                    context_event.title,
                    context_event.body,
                    context_event.source,
                    context_event.kind,
                ),
            )
            for entity in context_event.entities:
                entity_id = self._upsert_entity(conn, entity)
                conn.execute(
                    """
                    INSERT INTO entity_mentions(event_id, entity_id, confidence)
                    VALUES (?, ?, ?)
                    ON CONFLICT(event_id, entity_id) DO UPDATE SET confidence = excluded.confidence
                    """,
                    (context_event.id, entity_id, float(entity.get("confidence", 1.0))),
                )
        return context_event

    def _upsert_entity(self, conn: sqlite3.Connection, entity: dict[str, Any]) -> int:
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

    def get_event(self, event_id: str) -> ContextEvent | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM context_events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def recent_events(self, limit: int = 50) -> list[ContextEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM context_events ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def search_keyword(self, query: str, limit: int = 10) -> list[tuple[ContextEvent, float]]:
        terms = re.findall(r"[A-Za-z0-9_]+", query)
        if not terms:
            return []
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

    def _row_to_event(self, row: sqlite3.Row) -> ContextEvent:
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
            metadata=_json_load(row["metadata_json"], {}),
        )

