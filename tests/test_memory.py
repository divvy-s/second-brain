from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from connectors.base import ContextEvent
from memory.database import MemoryDatabase
from memory.knowledge_graph import KnowledgeGraph
from memory.ner import EntityExtractor
from memory.retrieval import HybridRetriever
from memory.vector_store import VectorStore


class MemoryTests(unittest.TestCase):
    def test_event_storage_fts_vector_and_graph(self) -> None:
        tmp_path = Path.cwd() / ".test_runs" / str(uuid.uuid4())
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = MemoryDatabase(tmp_path / "memory.sqlite3")
        database.initialize()
        extractor = EntityExtractor()
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
        hybrid_hits = HybridRetriever(database, vector_store).retrieve("launch security", limit=5)
        self.assertEqual(hybrid_hits[0].event.id, event.id)


if __name__ == "__main__":
    unittest.main()
