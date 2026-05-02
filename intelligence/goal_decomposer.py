from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMUnavailable


@dataclass(frozen=True)
class GoalStep:
    title: str
    owner: str
    risk: str
    action: dict[str, Any]


class GoalDecomposer:
    def __init__(self, llm: LLMAdapter) -> None:
        self.llm = llm

    def decompose(self, goal: str, context: str = "") -> list[GoalStep]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You decompose user goals into executable Second Brain actions. "
                    "Return strict JSON with a top-level steps array. Each step must include "
                    "title, owner, risk (low|medium|high), and action."
                ),
            },
            {"role": "user", "content": f"Goal:\n{goal}\n\nContext:\n{context}"},
        ]
        try:
            response = self.llm.complete(LLMRequest(messages=messages, temperature=0.1, max_tokens=1200))
            return self._parse_steps(response.content)
        except (LLMUnavailable, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._rule_based(goal)

    def _parse_steps(self, raw: str) -> list[GoalStep]:
        match = re.search(r"\{.*\}", raw, re.S)
        payload = json.loads(match.group(0) if match else raw)
        steps = []
        for item in payload["steps"]:
            steps.append(
                GoalStep(
                    title=str(item["title"]),
                    owner=str(item.get("owner", "task_agent")),
                    risk=str(item.get("risk", "medium")),
                    action=dict(item.get("action") or {"type": "create_task", "title": item["title"]}),
                )
            )
        return steps

    def _rule_based(self, goal: str) -> list[GoalStep]:
        parts = [part.strip(" .") for part in re.split(r"\band then\b|[.;\n]+", goal, flags=re.I) if part.strip()]
        if not parts:
            parts = [goal.strip()]
        steps: list[GoalStep] = []
        for part in parts:
            lowered = part.lower()
            if any(word in lowered for word in ("email", "reply", "send")):
                owner = "email_agent"
                action_type = "draft_email"
            elif any(word in lowered for word in ("calendar", "meeting", "schedule")):
                owner = "calendar_agent"
                action_type = "create_calendar_draft"
            else:
                owner = "task_agent"
                action_type = "create_task"
            steps.append(GoalStep(part[:120], owner, "medium", {"type": action_type, "title": part}))
        return steps

