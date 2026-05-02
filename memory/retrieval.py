from __future__ import annotations

from dataclasses import dataclass

from connectors.base import ContextEvent
from memory.database import MemoryDatabase
from memory.decay import MemoryDecay
from memory.vector_store import VectorStore


@dataclass(frozen=True)
class RetrievalHit:
    event: ContextEvent
    score: float
    vector_score: float
    keyword_score: float


class HybridRetriever:
    def __init__(
        self,
        database: MemoryDatabase,
        vector_store: VectorStore,
        decay: MemoryDecay | None = None,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.decay = decay or MemoryDecay()
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

    def retrieve(self, query: str, limit: int = 10) -> list[RetrievalHit]:
        vector_hits = {hit["event_id"]: float(hit["score"]) for hit in self.vector_store.query(query, limit * 3)}
        keyword_hits = {event.id: score for event, score in self.database.search_keyword(query, limit * 3)}
        event_ids = set(vector_hits) | set(keyword_hits)
        hits: list[RetrievalHit] = []
        for event_id in event_ids:
            event = self.database.get_event(event_id)
            if event is None:
                continue
            vector_score = vector_hits.get(event_id, 0.0)
            keyword_score = keyword_hits.get(event_id, 0.0)
            recency = self.decay.retention(event)
            score = (self.vector_weight * vector_score) + (self.keyword_weight * keyword_score)
            score = score * (0.5 + 0.5 * recency) * (0.5 + 0.5 * event.importance)
            hits.append(RetrievalHit(event, score, vector_score, keyword_score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

