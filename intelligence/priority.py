from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from connectors.base import ContextEvent, ensure_aware


URGENT_TERMS = {
    "urgent": 0.18,
    "asap": 0.16,
    "blocked": 0.14,
    "deadline": 0.12,
    "today": 0.1,
    "tomorrow": 0.08,
    "approval": 0.08,
    "invoice": 0.06,
    "security": 0.06,
}


class PriorityScorer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.vip_terms = {item.lower() for item in self.config.get("vip_participants", [])}

    def score(self, event: ContextEvent, at: datetime | None = None) -> float:
        reference = ensure_aware(at or datetime.now(timezone.utc))
        occurred = ensure_aware(event.occurred_at)
        age_hours = max(0.0, (reference - occurred).total_seconds() / 3600.0)
        recency = max(0.0, 1.0 - (age_hours / 168.0))
        text = f"{event.title} {event.body}".lower()
        urgency = sum(weight for term, weight in URGENT_TERMS.items() if re.search(rf"\b{re.escape(term)}\b", text))
        vip = 0.0
        for participant in event.participants:
            if participant.lower() in self.vip_terms:
                vip = max(vip, 0.12)
        source_boosts = self.config.get("source_boosts", {})
        source_boost = float(source_boosts.get(event.source, 0.0))
        score = (0.45 * event.importance) + (0.25 * recency) + urgency + vip + source_boost
        return max(0.0, min(1.0, score))

