from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class NotificationAgent(BaseAgent):
    name = "notification_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        text = f"{event.title} {event.body}".lower()
        return any(term in text for term in ("urgent", "asap", "blocked", "approval", "whatsapp", "telegram", "message", "text"))

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        text = f"{event.title} {event.body}".lower()
        
        # Default to telegram, but switch if whatsapp is mentioned
        plugin = "telegram"
        if "whatsapp" in text or "wa" in text:
            plugin = "whatsapp"
            
        return AgentDecision(
            self.name,
            event.id,
            [
                {
                    "type": "send_message",
                    "plugin": plugin,
                    "source_event_id": event.id,
                    "risk": "medium",
                    "text": event.body if event.kind == "brain_dump" else f"Second Brain notification: {event.title}",
                }
            ],
            [f"Notification action generated for {plugin}."],
        )

