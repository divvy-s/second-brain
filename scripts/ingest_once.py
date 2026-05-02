from __future__ import annotations

from pathlib import Path

from connectors.config import load_config
from connectors.runner import ConnectorRunner
from memory.database import MemoryDatabase
from memory.event_bus import EventBus
from memory.knowledge_graph import KnowledgeGraph
from memory.ner import EntityExtractor
from memory.vector_store import VectorStore


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    database = MemoryDatabase(root / memory_config.get("sqlite_path", "data/second_brain.sqlite3"))
    database.initialize()
    vector_store = VectorStore(root / memory_config.get("chroma_path", "data/chroma"))
    bus = EventBus(memory_config.get("redis_url", "redis://localhost:6379/0"))
    extractor = EntityExtractor(memory_config.get("spacy_model", "en_core_web_sm"))
    graph = KnowledgeGraph(database)
    runner = ConnectorRunner()
    count = 0
    for event in runner.fetch_all_events():
        event.entities = [entity.to_dict() for entity in extractor.extract(f"{event.title}\n{event.body}")]
        database.add_event(event)
        vector_store.index_event(event)
        graph.link_event(event)
        bus.publish(event)
        count += 1
    print(f"Ingested {count} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

