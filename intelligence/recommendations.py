from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.retrieval import RetrievalHit


@dataclass(frozen=True)
class RecommendationItem:
    """A concrete suggestion produced from a ranked event."""

    kind: str
    score: float
    title: str
    rationale: str
    preview: str
    action: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "score": self.score,
            "title": self.title,
            "rationale": self.rationale,
            "preview": self.preview,
            "action": self.action,
        }


class RecommendationEngine:
    """Translate ranked memory hits into concrete proactive suggestions."""

    TASK_TERMS = ("todo", "task", "blocked", "deadline", "need to", "must", "review", "finish")
    REPLY_TERMS = ("reply", "respond", "confirm", "approve", "feedback")
    SCHEDULE_TERMS = ("meeting", "schedule", "calendar", "appointment", "call", "sync")

    def generate(self, hit: RetrievalHit, context_bundle: dict[str, Any]) -> dict[str, Any]:
        """Build reply, task, follow-up, and scheduling suggestions for one hit."""
        structured = context_bundle.get("structured", {}) if isinstance(context_bundle, dict) else {}
        contacts = list(structured.get("contacts", []))
        preferences = list(structured.get("preferences", []))
        entities = list(structured.get("entities", []))
        items: list[RecommendationItem] = []

        if self._should_reply(hit):
            items.append(self._reply_item(hit, contacts))
        if self._should_schedule(hit):
            items.append(self._schedule_item(hit, preferences))
        if self._should_task(hit):
            items.append(self._task_item(hit))
        if self._should_follow_up(hit, contacts):
            items.append(self._follow_up_item(hit, contacts))
        if not items:
            items.append(self._task_item(hit, title_prefix="Review"))

        items.sort(key=lambda item: item.score, reverse=True)
        primary = items[0]
        return {
            "event_id": hit.event.id,
            "score": hit.score,
            "priority_score": hit.priority_score,
            "category": primary.kind,
            "next_best_action": primary.title,
            "items": [item.to_dict() for item in items],
            "contacts": contacts,
            "preferences": preferences,
            "entities": entities,
        }

    def _should_reply(self, hit: RetrievalHit) -> bool:
        text = self._event_text(hit)
        return hit.event.kind == "email" or hit.event.source == "mcp_gmail" or any(term in text for term in self.REPLY_TERMS)

    def _should_schedule(self, hit: RetrievalHit) -> bool:
        text = self._event_text(hit)
        return hit.event.kind == "event" or any(term in text for term in self.SCHEDULE_TERMS)

    def _should_task(self, hit: RetrievalHit) -> bool:
        text = self._event_text(hit)
        return hit.event.kind in {"task", "brain_dump"} or hit.components.get("urgency", 0.0) >= 0.55 or any(term in text for term in self.TASK_TERMS)

    def _should_follow_up(self, hit: RetrievalHit, contacts: list[dict[str, Any]]) -> bool:
        return bool(contacts or hit.event.participants) and hit.score >= 0.45

    def _reply_item(self, hit: RetrievalHit, contacts: list[dict[str, Any]]) -> RecommendationItem:
        contact = self._best_contact(contacts)
        recipient = str(contact.get("email") or "")
        summary = hit.summary or hit.event.body or hit.event.title
        return RecommendationItem(
            kind="reply",
            score=min(1.0, hit.score + 0.12),
            title=f"Draft a reply for {hit.event.title}",
            rationale="Email-style context or explicit response language was detected.",
            preview=summary[:220],
            action={
                "type": "draft_email",
                "plugin": "gmail",
                "to": recipient,
                "subject": f"Re: {hit.event.title}",
                "body": f"Draft a concise response covering: {summary}",
            },
        )

    def _schedule_item(self, hit: RetrievalHit, preferences: list[dict[str, Any]]) -> RecommendationItem:
        lead_hint = ""
        for preference in preferences:
            if str(preference.get("key", "")).lower() == "meeting":
                lead_hint = f" Respect preference: {preference.get('value')}."
                break
        return RecommendationItem(
            kind="schedule",
            score=min(1.0, hit.score + 0.08),
            title=f"Prepare a calendar draft for {hit.event.title}",
            rationale="Calendar-like language suggests a scheduling follow-through.",
            preview=f"{hit.event.title}. {hit.summary}".strip()[:220],
            action={
                "type": "create_calendar_draft",
                "plugin": "calendar",
                "title": hit.event.title,
                "description": f"{hit.summary or hit.event.body}{lead_hint}",
            },
        )

    def _task_item(self, hit: RetrievalHit, title_prefix: str = "Create") -> RecommendationItem:
        title = hit.event.title if len(hit.event.title.strip()) >= 4 else hit.summary or hit.event.body or "Review context"
        return RecommendationItem(
            kind="task",
            score=hit.score,
            title=f"{title_prefix} task: {title[:90]}",
            rationale="The event has enough urgency or importance to preserve as a task.",
            preview=(hit.summary or hit.event.body or hit.event.title)[:220],
            action={
                "type": "create_task",
                "plugin": "todoist",
                "title": title[:120],
                "description": hit.summary or hit.event.body or hit.event.title,
            },
        )

    def _follow_up_item(self, hit: RetrievalHit, contacts: list[dict[str, Any]]) -> RecommendationItem:
        contact = self._best_contact(contacts)
        counterpart = (
            str(contact.get("name") or "")
            or str(contact.get("email") or "")
            or (hit.event.participants[0] if hit.event.participants else "the relevant contact")
        )
        summary = hit.summary or hit.event.body or hit.event.title
        return RecommendationItem(
            kind="follow_up",
            score=min(1.0, hit.score + 0.05),
            title=f"Follow up with {counterpart}",
            rationale="A specific person or contact was detected in the ranked context.",
            preview=summary[:220],
            action={
                "type": "create_task",
                "plugin": "todoist",
                "title": f"Follow up with {counterpart}",
                "description": f"Follow up regarding: {summary}",
            },
        )

    def _best_contact(self, contacts: list[dict[str, Any]]) -> dict[str, Any]:
        if not contacts:
            return {}
        return max(contacts, key=lambda item: float(item.get("importance", 0.0)))

    def _event_text(self, hit: RetrievalHit) -> str:
        return " ".join(
            part.lower()
            for part in (hit.event.title, hit.summary, hit.event.body)
            if part and re.search(r"\w", part)
        )
