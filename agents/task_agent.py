from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class TaskAgent(BaseAgent):
    name = "task_agent"

    def can_handle(self, event: ContextEvent) -> bool:
        text = f"{event.title} {event.body}".lower()
        return event.importance >= 0.55 or any(term in text for term in ("todo", "task", "blocked", "deadline"))

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        return AgentDecision(
            self.name,
            event.id,
            [
                {
                    "type": "create_task",
                    "plugin": "todoist",
                    "source_event_id": event.id,
                    "risk": "medium",
                    "title": event.title,
                    "description": event.body,
                }
            ],
            ["Task candidate generated from important context."],
        )

