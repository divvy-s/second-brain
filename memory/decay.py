from __future__ import annotations

import math
from datetime import datetime, timezone

from connectors.base import ContextEvent, ensure_aware


class MemoryDecay:
    def __init__(self, half_life_days: float = 30.0) -> None:
        self.half_life_days = max(1.0, half_life_days)

    def reinforcement_for_access(
        self,
        access_count: int,
        *,
        retrieval_count: int = 0,
        planning_count: int = 0,
        execution_count: int = 0,
    ) -> float:
        weighted = (
            max(0, access_count)
            + (0.5 * max(0, retrieval_count))
            + (1.5 * max(0, planning_count))
            + (2.0 * max(0, execution_count))
        )
        return max(0.0, min(1.0, math.log1p(weighted) / 4.0))

    def retention(self, event: ContextEvent, at: datetime | None = None, reinforcement: float = 0.0) -> float:
        reference = ensure_aware(at or datetime.now(timezone.utc))
        occurred = ensure_aware(event.occurred_at)
        age_days = max(0.0, (reference - occurred).total_seconds() / 86400.0)
        decay = math.exp(-math.log(2) * age_days / self.half_life_days)
        boosted = decay * (1.0 + max(0.0, reinforcement))
        return max(0.0, min(1.0, boosted))

    def weighted_importance(self, event: ContextEvent, at: datetime | None = None, reinforcement: float = 0.0) -> float:
        return event.importance * self.retention(event, at=at, reinforcement=reinforcement)

