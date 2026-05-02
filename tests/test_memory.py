from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from connectors.base import ContextEvent
from intelligence.priority import PriorityScorer
from memory.database import MemoryDatabase
from memory.knowledge_graph import KnowledgeGraph
from memory.ner import EntityExtractor
from memory.retrieval import HybridRetriever
from memory.vector_store import VectorStore


class MemoryTests(unittest.TestCase):
    def test_event_storage_fts_vector_graph_and_structured_retrieval(self) -> None:
        tmp_path = Path.cwd() / ".test_runs" / str(uuid.uuid4())
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = MemoryDatabase(tmp_path / "memory.sqlite3")
        database.initialize()
        extractor = EntityExtractor()
        database.upsert_contact(name="Devon", email="devon@example.com", importance=0.95, metadata={"role": "approver"})
        database.set_preference("meeting", {"preferred_lead_time_hours": 24})
        database.upsert_entity("organization", "Launch Team", {"tier": "critical"}, context_text="Owns launch readiness", importance=0.9)
        event = ContextEvent(
            source="mcp_slack",
            kind="message",
            title="Security review blocked",
            body="Devon says launch is blocked until asha@example.com approves the security checklist.",
            participants=["devon"],
            importance=0.8,
        )
        event.entities = [entity.to_dict() for entity in extractor.extract(event.body)]
        database.add_event(event)
        KnowledgeGraph(database).link_event(event)
        vector_store = VectorStore(tmp_path / "vectors")
        vector_store.index_event(event)
        keyword_hits = database.search_keyword("security checklist", limit=5)
        self.assertEqual(keyword_hits[0][0].id, event.id)
        result = HybridRetriever(database, vector_store, scorer=PriorityScorer(), extractor=extractor).retrieve("launch security with Devon", limit=5)
        self.assertEqual(result[0].event.id, event.id)
        self.assertTrue(result.structured.contacts)
        self.assertEqual(database.get_contact_by_name("Devon")["email"], "devon@example.com")
        self.assertEqual(database.get_preference("meeting")["preferred_lead_time_hours"], 24)
        self.assertTrue(database.search_entities("Launch Team"))
        self.assertGreaterEqual(database.get_event_stats(event.id).retrieval_count, 1)


if __name__ == "__main__":
    unittest.main()
