"""
Background tasks for the ARQ worker.

These run outside the FastAPI process on a schedule, so they don't block
incoming HTTP requests. Requires a running Redis server.
"""
from __future__ import annotations

import logging
from pathlib import Path

from connectors.config import load_config
from connectors.runner import ConnectorRunner, PluginRegistry
from memory.database import MemoryDatabase
from memory.vector_store import VectorStore

logger = logging.getLogger(__name__)


async def fetch_emails_task(ctx: dict) -> dict:
    """Fetch events from all enabled plugins and store them in memory."""
    app_root = Path(__file__).resolve().parents[1]
    config = load_config(app_root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})

    database = MemoryDatabase(app_root / memory_config.get("sqlite_path", "data/second_brain.sqlite3"))
    database.initialize()
    vector_store = VectorStore(app_root / memory_config.get("chroma_path", "data/chroma"))
    registry = PluginRegistry(root_dir=app_root)
    runner = ConnectorRunner(registry)

    events = runner.fetch_all_events()
    stored_count = 0
    for event in events:
        stored = database.add_event(event)
        vector_store.index_event(stored)
        stored_count += 1

    logger.info("fetch_emails_task completed: %d events stored", stored_count)
    return {"stored": stored_count, "fetched": len(events)}
