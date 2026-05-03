from __future__ import annotations

from agents.base import AgentDecision, BaseAgent
from connectors.base import ContextEvent


class TaskAgent(BaseAgent):
    name = "task_agent"

    _KEYWORDS = ("todo", "task", "blocked", "deadline", "remind", "follow up", "need to", "must", "should", "buy", "fix", "review", "send", "complete", "finish")

    def can_handle(self, event: ContextEvent) -> bool:
        # Always handle manual brain dumps — that's the primary input
        if event.kind == "brain_dump":
            return True
        text = f"{event.title} {event.body}".lower()
        return event.importance >= 0.6 or any(term in text for term in self._KEYWORDS)

    def handle(self, event: ContextEvent, context: dict | None = None) -> AgentDecision:
        # Use the body as the task title when it's a short brain dump
        title = event.title
        description = event.body
        if event.kind == "brain_dump" and len(event.body) < 120:
            title = event.body
            description = "Captured via Brain Dump"

        return AgentDecision(
            self.name,
            event.id,
            [
                {
                    "type": "create_task",
                    "plugin": "todoist",
                    "source_event_id": event.id,
                    "risk": "medium",
                    "title": title,
                    "description": description,
                }
            ],
            ["Task queued for Todoist from brain dump."],
        )
