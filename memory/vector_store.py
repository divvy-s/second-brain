from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent


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
        self._collection = self._connect_chroma()
        self._local_path = self.path / "local_vector_store.json"
        self._local_items = self._load_local_items()

    def _connect_chroma(self) -> Any | None:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(self.path))
            return client.get_or_create_collection(self.collection_name)
        except Exception:
            return None

    def _load_local_items(self) -> dict[str, dict[str, Any]]:
        if not self._local_path.exists():
            return {}
        return json.loads(self._local_path.read_text(encoding="utf-8"))

    def _save_local_items(self) -> None:
        self._local_path.write_text(json.dumps(self._local_items, indent=2), encoding="utf-8")

    def index_event(self, event: ContextEvent) -> None:
        document = f"{event.title}\n{event.body}"
        metadata = {
            "source": event.source,
            "kind": event.kind,
            "occurred_at": event.occurred_at.isoformat(),
            "importance": event.importance,
        }
        if self._collection is not None:
            try:
                self._collection.delete(ids=[event.id])
            except Exception:
                pass
            self._collection.add(ids=[event.id], documents=[document], metadatas=[metadata])
        self._local_items[event.id] = {"document": document, "metadata": metadata}
        self._save_local_items()

    def query(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self._collection is not None:
            try:
                result = self._collection.query(query_texts=[query], n_results=limit)
                ids = result.get("ids", [[]])[0]
                distances = result.get("distances", [[]])[0]
                metadatas = result.get("metadatas", [[]])[0]
                return [
                    {
                        "event_id": event_id,
                        "score": 1.0 / (1.0 + float(distance or 0.0)),
                        "metadata": metadata or {},
                    }
                    for event_id, distance, metadata in zip(ids, distances, metadatas)
                ]
            except Exception:
                pass
        query_vector = Counter(_tokens(query))
        scored = []
        for event_id, item in self._local_items.items():
            score = _cosine(query_vector, Counter(_tokens(item["document"])))
            if score > 0:
                scored.append({"event_id": event_id, "score": score, "metadata": item.get("metadata", {})})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]

