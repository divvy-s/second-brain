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


async def voice_briefing_task(ctx: dict) -> dict:
    """
    Heartbeat task: trigger a TTS morning briefing for any urgent pending items.

    Runs every 30 minutes (matching the heartbeat cycle).
    Voice layer is lazy-imported — if deleted, this task silently no-ops.
    """
    app_root = Path(__file__).resolve().parents[1]
    config = load_config(app_root / "config" / "user_config.yml")

    # Only run if voice is enabled in config
    voice_cfg = config.get("voice", {})
    if not voice_cfg.get("enabled", False):
        logger.debug("voice_briefing_task: voice not enabled in config; skipping.")
        return {"status": "disabled"}

    try:
        import sys
        sys.path.insert(0, str(app_root))
        from voice import VoiceService  # type: ignore[import]
    except ImportError:
        logger.debug("voice_briefing_task: voice package not installed; skipping.")
        return {"status": "voice_not_installed"}

    # Load approval store to find urgent pending items
    from execution import ApprovalStore
    from memory.database import MemoryDatabase as DB

    database = DB(app_root / config.get("memory", {}).get("sqlite_path", "data/second_brain.sqlite3"))
    database.initialize()
    approval_store = ApprovalStore(database)
    pending = approval_store.list("pending")

    threshold = float(voice_cfg.get("tts_priority_threshold", 0.80))
    urgent = [r for r in pending if float(r.action.get("priority_score", 0.0)) >= threshold]

    if not urgent:
        logger.info("voice_briefing_task: no urgent items above threshold=%.2f", threshold)
        return {"status": "no_urgent_items"}

    top = urgent[:3]
    lines = [
        f"{idx + 1}. {r.action.get('title') or r.action.get('type', 'Action')} — "
        f"score: {r.action.get('priority_score', 0.0):.2f}"
        for idx, r in enumerate(top)
    ]
    briefing_text = "Heartbeat alert! Urgent items requiring your attention: " + "; ".join(lines)

    voice_svc = VoiceService(config)
    cache_key = await voice_svc.speak(briefing_text, force=True)
    logger.info("voice_briefing_task: TTS cache_key=%s for %d items", cache_key, len(top))
    return {"status": "briefing_generated", "cache_key": cache_key, "items": len(top)}
