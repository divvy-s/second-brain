from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class NotificationAgent(BaseAgent):
    name = "notification_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        text = f"{event.title} {event.body}".lower()
        return any(term in text for term in ("urgent", "asap", "blocked", "approval"))

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        return AgentDecision(
            self.name,
            event.id,
            [
                {
                    "type": "send_message",
                    "plugin": "telegram",
                    "source_event_id": event.id,
                    "risk": "medium",
                    "text": f"Second Brain priority: {event.title}",
                }
            ],
            ["Notification action generated for urgent context."],
        )

