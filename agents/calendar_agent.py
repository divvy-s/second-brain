from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class CalendarAgent(BaseAgent):
    name = "calendar_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        text = f"{event.title} {event.body}".lower()
        return any(term in text for term in ("meeting", "calendar", "schedule", "call"))

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        return AgentDecision(
            self.name,
            event.id,
            [
                {
                    "type": "create_calendar_draft",
                    "plugin": "calendar",
                    "source_event_id": event.id,
                    "risk": "medium",
                    "title": event.title,
                    "description": event.body,
                }
            ],
            ["Calendar draft requires a calendar plugin before execution."],
        )

