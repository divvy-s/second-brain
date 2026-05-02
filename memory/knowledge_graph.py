from __future__ import annotations

from itertools import combinations

from connectors.base import ContextEvent
from memory.database import MemoryDatabase


class KnowledgeGraph:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def link_event(self, event: ContextEvent) -> None:
        event_node = f"event:{event.id}"
        self.database.upsert_node(
            event_node,
            "event",
            event.title,
            {"source": event.source, "kind": event.kind, "occurred_at": event.occurred_at.isoformat()},
        )
        entity_nodes: list[str] = []
        for entity in event.entities:
            label = str(entity.get("label", "ENTITY"))
            value = str(entity.get("normalized_value") or entity.get("value", "")).lower()
            if not value:
                continue
            node_id = f"entity:{label}:{value}"
            entity_nodes.append(node_id)
            self.database.upsert_node(node_id, "entity", str(entity.get("value", value)), {"label": label})
            self.database.upsert_edge(event_node, node_id, "MENTIONS", float(entity.get("confidence", 1.0)))
        for left, right in combinations(sorted(set(entity_nodes)), 2):
            self.database.upsert_edge(left, right, "CO_OCCURS", 1.0, {"event_id": event.id})
            self.database.upsert_edge(right, left, "CO_OCCURS", 1.0, {"event_id": event.id})

