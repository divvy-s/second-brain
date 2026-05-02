from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class EmailAgent(BaseAgent):
    name = "email_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        return event.kind == "email" or event.source == "mcp_gmail"

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        text = f"{event.title} {event.body}".lower()
        actions = []
        notes = []
        if any(term in text for term in ("reply", "feedback", "confirm", "approve")):
            actions.append(
                {
                    "type": "draft_email",
                    "plugin": "gmail",
                    "source_event_id": event.id,
                    "risk": "low",
                    "subject": f"Re: {event.title}",
                    "body": "Draft a concise response that addresses the request.",
                }
            )
        else:
            notes.append("No email action needed.")
        return AgentDecision(self.name, event.id, actions, notes)

