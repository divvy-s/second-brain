from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from connectors.base import ContextEvent
from intelligence.priority import PriorityScorer
from memory.database import EventMemoryStats, MemoryDatabase
from memory.decay import MemoryDecay
from memory.ner import Entity, EntityExtractor
from memory.vector_store import VectorStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalHit:
    event: ContextEvent
    score: float
    vector_score: float
    keyword_score: float
    priority_score: float
    decay_factor: float
    reinforcement: float
    summary: str
    components: dict[str, Any]


@dataclass(frozen=True)
class StructuredRetrievalContext:
    contacts: list[dict[str, Any]]
    preferences: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    query_entities: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contacts": self.contacts,
            "preferences": self.preferences,
            "entities": self.entities,
            "query_entities": self.query_entities,
        }


@dataclass(frozen=True)
class HybridRetrievalResult:
    hits: list[RetrievalHit]
    structured: StructuredRetrievalContext

    def __iter__(self):
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    def __getitem__(self, index: int) -> RetrievalHit:
        return self.hits[index]


class HybridRetriever:
    def __init__(
        self,
        database: MemoryDatabase,
        vector_store: VectorStore,
        decay: MemoryDecay | None = None,
        scorer: PriorityScorer | None = None,
        extractor: EntityExtractor | None = None,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.25,
        entity_overlap_weight: float = 0.15,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.decay = decay or MemoryDecay()
        self.scorer = scorer or PriorityScorer()
        self.extractor = extractor or EntityExtractor()
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.entity_overlap_weight = entity_overlap_weight

    def retrieve(self, query: str, limit: int = 10) -> HybridRetrievalResult:
        structured = self._structured_context(query)
        if not getattr(self.vector_store, "ready", False):
            logger.info("Vector search is not ready; using SQLite keyword fallback for memory search.")
        try:
            vector_hits = {
                hit["event_id"]: hit
                for hit in self.vector_store.query(query, max(limit * 4, 12))
            }
        except Exception as exc:
            logger.warning("Vector search failed; using SQLite keyword fallback: %s", exc)
            vector_hits = {}
        keyword_hits = {
            event.id: score
            for event, score in self.database.search_keyword(query, max(limit * 4, 12))
        }
        event_ids = list(set(vector_hits) | set(keyword_hits))
        if not event_ids:
            event_ids = [event.id for event in self.database.ranked_events(max(limit * 3, 12))]
        events_by_id = self.database.get_events_by_ids(event_ids)
        stats_by_id = self.database.get_event_stats_map(event_ids)
        ranked_hits = [
            self._build_hit(
                events_by_id.get(event_id),
                query=query,
                structured=structured,
                vector_hit=vector_hits.get(event_id),
                keyword_score=keyword_hits.get(event_id, 0.0),
                stats=stats_by_id.get(event_id),
            )
            for event_id in event_ids
        ]
        hits = [hit for hit in ranked_hits if hit is not None]
        hits.sort(key=lambda hit: (hit.score, hit.priority_score, hit.vector_score, hit.keyword_score), reverse=True)
        selected = hits[:limit]
        for hit in selected:
            self.database.increment_event_access(hit.event.id, reason="retrieval")
        return HybridRetrievalResult(selected, structured)

    def rank_events(
        self,
        events: Iterable[ContextEvent] | None = None,
        *,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        candidate_events = list(events) if events is not None else self.database.all_events(limit=limit)
        stats_by_id = self.database.get_event_stats_map([event.id for event in candidate_events])
        hits = [
            self._build_hit(
                event,
                query=None,
                structured=self._structured_context(f"{event.title}\n{event.body}"),
                vector_hit=None,
                keyword_score=0.0,
                stats=stats_by_id.get(event.id),
            )
            for event in candidate_events
        ]
        ranked = [hit for hit in hits if hit is not None]
        ranked.sort(key=lambda hit: (hit.score, hit.priority_score, hit.event.occurred_at), reverse=True)
        if limit is not None:
            ranked = ranked[:limit]
        return ranked

    def _build_hit(
        self,
        event: ContextEvent | None,
        *,
        query: str | None,
        structured: StructuredRetrievalContext,
        vector_hit: dict[str, Any] | None,
        keyword_score: float,
        stats: EventMemoryStats | None = None,
    ) -> RetrievalHit | None:
        if event is None:
            return None
        stats = stats or self.database.get_event_stats(event.id)
        summary = (
            stats.semantic_summary
            or str(event.metadata.get("semantic_summary") or "")
            or event.body
            or event.title
        ).strip()
        vector_score = float((vector_hit or {}).get("score", 0.0))
        overlap_score = self._entity_overlap_score(structured.query_entities, event)
        explicit_relevance = self._clamp(
            (self.vector_weight * vector_score)
            + (self.keyword_weight * float(keyword_score or 0.0))
            + (self.entity_overlap_weight * overlap_score)
        )
        contact_matches = self._match_contacts_to_event(structured.contacts, event)
        entity_matches = self._match_entities_to_event(structured.entities, event)
        preference_matches = self._match_preferences_to_event(structured.preferences, event, query=query)
        reinforcement = stats.reinforcement or self.decay.reinforcement_for_access(
            stats.access_count,
            retrieval_count=stats.retrieval_count,
            planning_count=stats.planning_count,
            execution_count=stats.execution_count,
        )
        decay_factor = self.decay.retention(event, reinforcement=reinforcement)
        breakdown = self.scorer.score_breakdown(
            event,
            query=query,
            relevance=explicit_relevance if explicit_relevance > 0 else None,
            access_count=stats.access_count,
            retrieval_count=stats.retrieval_count,
            planning_count=stats.planning_count,
            execution_count=stats.execution_count,
            contact_matches=contact_matches,
            preference_matches=preference_matches,
            entity_matches=entity_matches,
            decay_factor=decay_factor,
        )
        components = breakdown.to_dict()
        self.database.update_event_ranking(
            event.id,
            priority_score=breakdown.priority_score,
            decay_factor=decay_factor,
            effective_score=breakdown.effective_score,
            reinforcement=reinforcement,
            priority_components=components,
            semantic_summary=summary,
        )
        event.metadata["semantic_summary"] = summary
        event.metadata["ranking"] = {
            "priority_score": breakdown.priority_score,
            "decay_factor": decay_factor,
            "effective_score": breakdown.effective_score,
            "reinforcement": reinforcement,
            "access_count": stats.access_count,
            "retrieval_count": stats.retrieval_count,
            "planning_count": stats.planning_count,
            "execution_count": stats.execution_count,
            "components": components,
        }
        self.vector_store.index_event(event, summary=summary)
        return RetrievalHit(
            event=event,
            score=breakdown.effective_score,
            vector_score=vector_score,
            keyword_score=float(keyword_score or 0.0),
            priority_score=breakdown.priority_score,
            decay_factor=decay_factor,
            reinforcement=reinforcement,
            summary=summary,
            components=components,
        )

    def _structured_context(self, query: str) -> StructuredRetrievalContext:
        extracted_entities = self.extractor.extract(query)
        query_entities = [entity.to_dict() for entity in extracted_entities]
        contacts = self._collect_contacts(query, extracted_entities)
        preferences = self.database.matching_preferences(query)
        entities = self._collect_entities(query, extracted_entities)
        return StructuredRetrievalContext(
            contacts=self._dedupe_records(contacts, key=lambda item: item.get("id") or item.get("email") or item.get("name")),
            preferences=self._dedupe_records(preferences, key=lambda item: item.get("key")),
            entities=self._dedupe_records(entities, key=lambda item: item.get("id") or f"{item.get('type')}:{item.get('name')}"),
            query_entities=query_entities,
        )

    def _collect_contacts(self, query: str, extracted_entities: list[Entity]) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        for entity in extracted_entities:
            if entity.label == "EMAIL":
                match = self.database.get_contact(entity.value)
                if match:
                    contacts.append(match)
                    continue
            if entity.label in {"PERSON", "PER", "NORP", "PROPER_NOUN"}:
                match = self.database.get_contact_by_name(entity.value)
                if match:
                    contacts.append(match)
        contacts.extend(self.database.search_contacts(query, limit=6))
        return contacts

    def _collect_entities(self, query: str, extracted_entities: list[Entity]) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        for entity in extracted_entities:
            entities.extend(self.database.search_entities(entity.value, limit=4))
        entities.extend(self.database.search_entities(query, limit=6))
        return entities

    def _match_contacts_to_event(self, contacts: list[dict[str, Any]], event: ContextEvent) -> list[dict[str, Any]]:
        haystack = self._event_text(event).lower()
        matches = []
        for contact in contacts:
            name = str(contact.get("name", "")).strip().lower()
            email = str(contact.get("email", "")).strip().lower()
            if (name and name in haystack) or (email and email in haystack):
                matches.append(contact)
                continue
            if any(name and name == participant.lower() for participant in event.participants):
                matches.append(contact)
        return self._dedupe_records(matches, key=lambda item: item.get("id") or item.get("email") or item.get("name"))

    def _match_entities_to_event(self, entities: list[dict[str, Any]], event: ContextEvent) -> list[dict[str, Any]]:
        event_text = self._event_text(event).lower()
        matches = []
        for entity in entities:
            name = str(entity.get("name", "")).strip().lower()
            if name and name in event_text:
                matches.append(entity)
        return self._dedupe_records(matches, key=lambda item: item.get("id") or f"{item.get('type')}:{item.get('name')}")

    def _match_preferences_to_event(
        self,
        preferences: list[dict[str, Any]],
        event: ContextEvent,
        *,
        query: str | None,
    ) -> list[dict[str, Any]]:
        event_text = self._event_text(event).lower()
        matches = []
        query_tokens = self._tokens(query or "")
        for preference in preferences:
            key = str(preference.get("key", "")).lower()
            if key and key in event_text:
                matches.append(preference)
                continue
            if key and key in query_tokens:
                matches.append(preference)
        return self._dedupe_records(matches, key=lambda item: item.get("key"))

    def _entity_overlap_score(self, query_entities: list[dict[str, Any]], event: ContextEvent) -> float:
        if not query_entities:
            return 0.0
        event_values = {
            str(entity.get("normalized_value") or entity.get("value", "")).lower()
            for entity in event.entities
            if str(entity.get("normalized_value") or entity.get("value", "")).strip()
        }
        query_values = {
            str(entity.get("normalized_value") or entity.get("value", "")).lower()
            for entity in query_entities
            if str(entity.get("normalized_value") or entity.get("value", "")).strip()
        }
        if not event_values or not query_values:
            return 0.0
        overlap = len(event_values & query_values)
        if overlap <= 0:
            return 0.0
        return self._clamp(overlap / max(len(query_values), 1))

    def _event_text(self, event: ContextEvent) -> str:
        summary = str(event.metadata.get("semantic_summary") or "")
        return "\n".join(part for part in [event.title, summary, event.body] if part).strip()

    def _tokens(self, text: str) -> set[str]:
        return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", text)}

    def _dedupe_records(self, items: list[dict[str, Any]], key) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in items:
            marker = str(key(item))
            if not marker or marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        return unique

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))
