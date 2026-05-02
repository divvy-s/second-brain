from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from connectors.base import ContextEvent, ensure_aware


URGENT_TERMS = {
    "urgent": 0.95,
    "asap": 0.9,
    "blocked": 0.82,
    "deadline": 0.78,
    "today": 0.74,
    "tomorrow": 0.66,
    "approval": 0.64,
    "invoice": 0.58,
    "security": 0.56,
    "immediately": 0.92,
    "follow up": 0.62,
}


@dataclass(frozen=True)
class PriorityBreakdown:
    recency: float
    relevance: float
    user_importance: float
    urgency: float
    frequency: float
    priority_score: float
    effective_score: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "recency": self.recency,
            "relevance": self.relevance,
            "user_importance": self.user_importance,
            "urgency": self.urgency,
            "frequency": self.frequency,
            "priority_score": self.priority_score,
            "effective_score": self.effective_score,
        }


class PriorityScorer:
    DEFAULT_WEIGHTS = {
        "recency": 0.22,
        "relevance": 0.27,
        "user_importance": 0.21,
        "urgency": 0.20,
        "frequency": 0.10,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.vip_terms = {item.lower() for item in self.config.get("vip_participants", [])}
        raw_weights = dict(self.DEFAULT_WEIGHTS)
        raw_weights.update(self.config.get("weights", {}))
        total = sum(max(0.0, float(value)) for value in raw_weights.values()) or 1.0
        self.weights = {key: max(0.0, float(value)) / total for key, value in raw_weights.items()}
        self.source_boosts = {str(key): float(value) for key, value in self.config.get("source_boosts", {}).items()}
        self.urgent_terms = dict(URGENT_TERMS)
        self.urgent_terms.update(
            {
                str(term).lower(): max(0.0, min(1.0, float(weight)))
                for term, weight in self.config.get("urgent_terms", {}).items()
            }
        )

    def score(self, event: ContextEvent, **kwargs: Any) -> float:
        return self.score_breakdown(event, **kwargs).priority_score

    def score_breakdown(
        self,
        event: ContextEvent,
        *,
        at: datetime | None = None,
        query: str | None = None,
        relevance: float | None = None,
        access_count: int = 0,
        retrieval_count: int = 0,
        planning_count: int = 0,
        execution_count: int = 0,
        contact_matches: list[dict[str, Any]] | None = None,
        preference_matches: list[dict[str, Any]] | None = None,
        entity_matches: list[dict[str, Any]] | None = None,
        decay_factor: float | None = None,
    ) -> PriorityBreakdown:
        recency = self._recency(event, at=at)
        relevance_score = self._relevance(
            event,
            query=query,
            explicit_relevance=relevance,
            preference_matches=preference_matches or [],
        )
        user_importance = self._user_importance(
            event,
            contact_matches=contact_matches or [],
            entity_matches=entity_matches or [],
        )
        urgency = self._urgency(event)
        frequency = self._frequency(
            access_count=access_count,
            retrieval_count=retrieval_count,
            planning_count=planning_count,
            execution_count=execution_count,
        )
        priority_score = self._clamp(
            (self.weights["recency"] * recency)
            + (self.weights["relevance"] * relevance_score)
            + (self.weights["user_importance"] * user_importance)
            + (self.weights["urgency"] * urgency)
            + (self.weights["frequency"] * frequency)
        )
        effective = self.effective_score(priority_score, 1.0 if decay_factor is None else decay_factor)
        return PriorityBreakdown(
            recency=recency,
            relevance=relevance_score,
            user_importance=user_importance,
            urgency=urgency,
            frequency=frequency,
            priority_score=priority_score,
            effective_score=effective,
        )

    def effective_score(self, priority_score: float, decay_factor: float) -> float:
        return self._clamp(priority_score) * self._clamp(decay_factor)

    def _recency(self, event: ContextEvent, *, at: datetime | None = None) -> float:
        reference = ensure_aware(at or datetime.now(timezone.utc))
        occurred = ensure_aware(event.occurred_at)
        age_hours = max(0.0, (reference - occurred).total_seconds() / 3600.0)
        return self._clamp(math.exp(-age_hours / 72.0))

    def _relevance(
        self,
        event: ContextEvent,
        *,
        query: str | None,
        explicit_relevance: float | None,
        preference_matches: list[dict[str, Any]],
    ) -> float:
        if explicit_relevance is not None:
            base = self._clamp(explicit_relevance)
        elif query:
            base = self._text_overlap_score(query, self._event_text(event))
        else:
            base = self._clamp(event.importance)
        if preference_matches:
            base = self._clamp(base + min(0.2, 0.05 * len(preference_matches)))
        source_boost = float(self.source_boosts.get(event.source, 0.0))
        return self._clamp(base + source_boost)

    def _user_importance(
        self,
        event: ContextEvent,
        *,
        contact_matches: list[dict[str, Any]],
        entity_matches: list[dict[str, Any]],
    ) -> float:
        importance = self._clamp(event.importance)
        if any(participant.lower() in self.vip_terms for participant in event.participants):
            importance = self._clamp(importance + 0.18)
        for contact in contact_matches:
            importance = max(importance, self._clamp(float(contact.get("importance", 0.5))))
        for entity in entity_matches:
            importance = max(importance, self._clamp(float(entity.get("importance", 0.5)) * 0.95))
        return self._clamp(importance)

    def _urgency(self, event: ContextEvent) -> float:
        text = self._event_text(event).lower()
        urgency = 0.0
        for term, weight in self.urgent_terms.items():
            if re.search(rf"\b{re.escape(term)}\b", text):
                urgency = max(urgency, weight)
        if re.search(r"\b\d{1,2}:\d{2}\b", text):
            urgency = max(urgency, 0.58)
        if re.search(r"\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", text):
            urgency = max(urgency, 0.55)
        return self._clamp(max(urgency, event.importance * 0.4))

    def _frequency(
        self,
        *,
        access_count: int,
        retrieval_count: int,
        planning_count: int,
        execution_count: int,
    ) -> float:
        weighted = (
            max(0, access_count)
            + (0.5 * max(0, retrieval_count))
            + (1.5 * max(0, planning_count))
            + (2.0 * max(0, execution_count))
        )
        if weighted <= 0:
            return 0.0
        return self._clamp(math.log1p(weighted) / 4.0)

    def _event_text(self, event: ContextEvent) -> str:
        semantic_summary = ""
        if isinstance(event.metadata.get("semantic_summary"), str):
            semantic_summary = str(event.metadata["semantic_summary"])
        return "\n".join(part for part in [event.title, semantic_summary, event.body] if part).strip()

    def _text_overlap_score(self, left: str, right: str) -> float:
        left_tokens = Counter(token.lower() for token in re.findall(r"[A-Za-z0-9_]+", left))
        right_tokens = Counter(token.lower() for token in re.findall(r"[A-Za-z0-9_]+", right))
        if not left_tokens or not right_tokens:
            return 0.0
        common = set(left_tokens) & set(right_tokens)
        if not common:
            return 0.0
        dot = sum(left_tokens[token] * right_tokens[token] for token in common)
        norm_left = math.sqrt(sum(value * value for value in left_tokens.values()))
        norm_right = math.sqrt(sum(value * value for value in right_tokens.values()))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return self._clamp(dot / (norm_left * norm_right))

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))
