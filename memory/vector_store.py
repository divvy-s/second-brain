from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent


logger = logging.getLogger(__name__)


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", text)]


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    dot = sum(left[token] * right[token] for token in common)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class VectorStore:
    def __init__(self, path: str | Path, collection_name: str = "context_events") -> None:
        self.path = Path(path)
        self.collection_name = collection_name
        self.path.mkdir(parents=True, exist_ok=True)
        self._local_path = self.path / "local_vector_store.json"
        self._local_items = self._load_local_items()
        self._client = None
        self._collection = None
        self._ready = asyncio.Event()

    def initialize(self) -> None:
        """Initialize ChromaDB connection synchronously."""
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(self.path))
            self._collection = self._client.get_or_create_collection(self.collection_name)
            self._ready.set()
        except Exception as exc:
            logger.warning("ChromaDB unavailable: %s", exc)

    async def initialize_async(self) -> None:
        """Initialize ChromaDB connection asynchronously."""
        await asyncio.to_thread(self.initialize)

    @property
    def ready(self) -> bool:
        return self._collection is not None and self._ready.is_set()

    def _load_local_items(self) -> dict[str, dict[str, Any]]:
        if not self._local_path.exists():
            return {}
        return json.loads(self._local_path.read_text(encoding="utf-8"))

    def _save_local_items(self) -> None:
        self._local_path.write_text(json.dumps(self._local_items, indent=2), encoding="utf-8")

    def index_event(self, event: ContextEvent, *, summary: str | None = None) -> None:
        ranking = event.metadata.get("ranking", {}) if isinstance(event.metadata.get("ranking"), dict) else {}
        semantic_summary = (summary or event.metadata.get("semantic_summary") or event.body or event.title).strip()
        document = f"{event.title}\n{semantic_summary}".strip()
        metadata = {
            "source": event.source,
            "kind": event.kind,
            "occurred_at": event.occurred_at.isoformat(),
            "importance": event.importance,
            "semantic_summary": semantic_summary,
            "priority_score": float(ranking.get("priority_score", 0.0)),
            "decay_factor": float(ranking.get("decay_factor", 1.0)),
            "effective_score": float(ranking.get("effective_score", 0.0)),
        }
        if self._collection is not None:
            try:
                self._collection.delete(ids=[event.id])
            except Exception:
                pass
            try:
                self._collection.add(ids=[event.id], documents=[document], metadatas=[metadata])
            except Exception as exc:
                logger.warning("ChromaDB index update failed for %s, keeping local fallback only: %s", event.id, exc)
        self._local_items[event.id] = {
            "document": document,
            "full_text": f"{event.title}\n{event.body}".strip(),
            "metadata": metadata,
        }
        self._save_local_items()

    def query(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self._collection is not None:
            try:
                result = self._collection.query(
                    query_texts=[query],
                    n_results=limit,
                    include=["documents", "metadatas", "distances"],
                )
                ids = result.get("ids", [[]])[0]
                distances = result.get("distances", [[]])[0]
                metadatas = result.get("metadatas", [[]])[0]
                documents = result.get("documents", [[]])[0]
                return [
                    {
                        "event_id": event_id,
                        "score": 1.0 / (1.0 + float(distance or 0.0)),
                        "document": document or "",
                        "metadata": metadata or {},
                    }
                    for event_id, distance, metadata, document in zip(ids, distances, metadatas, documents)
                ]
            except Exception:
                logger.warning("ChromaDB query failed, falling back to local vector search.", exc_info=True)
        query_vector = Counter(_tokens(query))
        scored = []
        for event_id, item in self._local_items.items():
            search_text = f"{item.get('document', '')}\n{item.get('full_text', '')}"
            score = _cosine(query_vector, Counter(_tokens(search_text)))
            if score > 0:
                scored.append(
                    {
                        "event_id": event_id,
                        "score": score,
                        "document": item.get("document", ""),
                        "metadata": item.get("metadata", {}),
                    }
                )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]
