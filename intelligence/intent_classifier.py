"""
Intent Classifier — the AI layer between Capture and Action.

Given raw text from a brain dump, classifies the user's intent and produces
a structured action dict ready for the Approval Gate.

Uses the LLM when available, falls back to rule-based heuristics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMUnavailable


@dataclass(frozen=True)
class ClassifiedIntent:
    """The result of classifying a brain dump."""
    intent: str                      # "send_message" | "create_task" | "create_calendar_event" | "send_email" | "note"
    plugin: str                      # "telegram" | "whatsapp" | "todoist" | "google_calendar" | "gmail" | ""
    confidence: float                # 0.0 - 1.0
    fields: dict[str, Any]           # extracted fields: recipient, text, title, date, etc.
    reasoning: str                   # why this classification was chosen


SYSTEM_PROMPT = """\
You are an intent classifier for a personal AI assistant called "Second Brain".
The user types a quick brain dump. Your job is to classify what they want to do.

Return ONLY valid JSON with these fields:
{
  "intent": "send_message" | "create_task" | "create_calendar_event" | "send_email" | "note",
  "plugin": "telegram" | "whatsapp" | "todoist" | "calendar" | "gmail" | "",
  "confidence": 0.0 to 1.0,
  "fields": {
    "recipient": "name of the person if messaging someone",
    "text": "the message body to send",
    "title": "task or event title",
    "description": "longer description if any",
    "start_time": "ISO8601 formatted start time if mentioned (e.g., 2026-05-02T15:00:00Z)",
    "end_time": "ISO8601 formatted end time if mentioned",
    "subject": "email subject if email"
  },
  "reasoning": "one sentence explaining your classification"
}

Rules:
- If the user says "message X", "tell X", "text X", "send X", "inform X", "let X know", "ping X", "notify X" → intent is "send_message"
- For send_message: default plugin is "telegram" unless the user says "whatsapp" or "wa" or "email"
- If the user says "task", "todo", "buy", "need to", "remind me", "add", "fix", "complete", "finish", "review" → intent is "create_task", plugin is "todoist"
- If the user says "schedule", "meeting", "calendar", "appointment", "book" → intent is "create_calendar_event", plugin is "calendar"
- If the user says "email", "mail" → intent is "send_email", plugin is "gmail"
- If nothing matches clearly → intent is "note", plugin is ""
- Extract the recipient name and the actual message content separately
- "message naman meeting at 5" → recipient is "naman", text is "meeting at 5"
- Always fill "text" or "title" with the core content
"""


class IntentClassifier:
    """Classifies brain dump text into structured intents using LLM with rule-based fallback."""

    def __init__(self, llm: LLMAdapter) -> None:
        self.llm = llm

    def classify(self, text: str) -> ClassifiedIntent:
        """Classify the user's text into a structured intent."""
        # Try LLM first
        if self.llm.is_configured():
            try:
                return self._llm_classify(text)
            except (LLMUnavailable, json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        # Fall back to rules
        return self._rule_classify(text)

    def _llm_classify(self, text: str) -> ClassifiedIntent:
        """Use the LLM to classify intent."""
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + f"\n\nCURRENT DATE/TIME: {datetime.now(timezone.utc).isoformat()}"
            },
            {"role": "user", "content": text},
        ]
        response = self.llm.complete(LLMRequest(
            messages=messages,
            temperature=0.05,
            max_tokens=500,
        ))
        # Parse JSON from the response
        raw = response.content
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)

        return ClassifiedIntent(
            intent=data.get("intent", "note"),
            plugin=data.get("plugin", ""),
            confidence=float(data.get("confidence", 0.5)),
            fields=data.get("fields", {}),
            reasoning=data.get("reasoning", "LLM classification"),
        )

    def _rule_classify(self, text: str) -> ClassifiedIntent:
        """Rule-based fallback when LLM is unavailable."""
        lower = text.lower().strip()

        # --- Send Message patterns ---
        msg_patterns = [
            r"^(?:message|msg|tell|text|send|inform|let|ping|notify|whatsapp|wa)\s+(\w+)\s+(.+)",
            r"^(?:message|msg|tell|text|send|inform|ping|notify|whatsapp|wa)\s+(\w+)\s+that\s+(.+)",
        ]
        for pattern in msg_patterns:
            m = re.match(pattern, lower, re.I)
            if m:
                recipient = m.group(1)
                body = m.group(2)
                # Determine plugin
                plugin = "telegram"
                if "whatsapp" in lower or " wa " in f" {lower} ":
                    plugin = "whatsapp"
                elif "email" in lower or "mail" in lower:
                    plugin = "gmail"
                return ClassifiedIntent(
                    intent="send_message",
                    plugin=plugin,
                    confidence=0.85,
                    fields={"recipient": recipient, "text": body},
                    reasoning=f"Matched message pattern: '{recipient}' → {plugin}",
                )

        # --- Calendar patterns ---
        cal_keywords = ("schedule", "meeting", "calendar", "appointment", "book")
        if any(kw in lower for kw in cal_keywords):
            return ClassifiedIntent(
                intent="create_calendar_event",
                plugin="calendar",
                confidence=0.75,
                fields={"title": text, "description": text},
                reasoning="Matched calendar keywords",
            )

        # --- Email patterns ---
        email_match = re.match(r"^(?:email|mail)\s+(\w+)\s+(.+)", lower, re.I)
        if email_match:
            return ClassifiedIntent(
                intent="send_email",
                plugin="gmail",
                confidence=0.8,
                fields={"recipient": email_match.group(1), "subject": email_match.group(2), "text": email_match.group(2)},
                reasoning="Matched email pattern",
            )

        # --- Task patterns (broad catch) ---
        task_keywords = ("todo", "task", "buy", "need to", "remind", "add", "fix", "complete", "finish", "review", "must", "should")
        if any(kw in lower for kw in task_keywords):
            return ClassifiedIntent(
                intent="create_task",
                plugin="todoist",
                confidence=0.75,
                fields={"title": text, "description": ""},
                reasoning="Matched task keywords",
            )

        # --- Default: treat everything as a task (brain dumps are actionable) ---
        return ClassifiedIntent(
            intent="create_task",
            plugin="todoist",
            confidence=0.5,
            fields={"title": text, "description": "Captured via brain dump"},
            reasoning="Default classification — treated as task",
        )

    def to_action(self, intent: ClassifiedIntent, source_event_id: str) -> dict[str, Any]:
        """Convert a ClassifiedIntent into an action dict for the Approval Gate."""
        action: dict[str, Any] = {
            "type": intent.intent,
            "plugin": intent.plugin,
            "source_event_id": source_event_id,
            "risk": "medium",
            "confidence": intent.confidence,
            "reasoning": intent.reasoning,
        }

        if intent.intent == "send_message":
            action["text"] = intent.fields.get("text", "")
            action["recipient"] = intent.fields.get("recipient", "")
            action["title"] = f"Message {intent.fields.get('recipient', '')} via {intent.plugin}"

        elif intent.intent == "create_task":
            action["title"] = intent.fields.get("title", "")
            action["description"] = intent.fields.get("description", "")

        elif intent.intent == "create_calendar_event":
            action["title"] = intent.fields.get("title", "")
            action["description"] = intent.fields.get("description", "")
            action["start_time"] = intent.fields.get("start_time", "")
            action["end_time"] = intent.fields.get("end_time", "")

        elif intent.intent == "send_email":
            action["recipient"] = intent.fields.get("recipient", "")
            action["subject"] = intent.fields.get("subject", "")
            action["text"] = intent.fields.get("text", "")
            action["title"] = f"Email {intent.fields.get('recipient', '')}"

        else:
            action["title"] = intent.fields.get("title", "Note")
            action["description"] = intent.fields.get("text", "")

        return action
