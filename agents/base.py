from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from connectors.base import ContextEvent


@dataclass(frozen=True)
class AgentDecision:
    agent: str
    event_id: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class BaseAgent:
    name = "base_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        return True

    def handle(self, event: ContextEvent, context: dict[str, Any] | None = None) -> AgentDecision:
        raise NotImplementedError

