from __future__ import annotations

from typing import Any

from connectors.base import ContextEvent


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _message_text(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "message")
    if message_type == "text":
        text = message.get("text")
        return str(text.get("body") or "") if isinstance(text, dict) else ""
    if message_type == "image":
        image = message.get("image")
        return str(image.get("caption") or "[image]") if isinstance(image, dict) else "[image]"
    if message_type == "button":
        button = message.get("button")
        return str(button.get("text") or "[button]") if isinstance(button, dict) else "[button]"
    return f"[{message_type}]"


def extract_whatsapp_events(payload: Any) -> tuple[list[ContextEvent], int]:
    if not isinstance(payload, dict):
        return [], 1

    events: list[ContextEvent] = []
    ignored = 0
    entries = _as_list(payload.get("entry"))
    if "entry" in payload and not entries:
        ignored += 1

    for entry in entries:
        if not isinstance(entry, dict):
            ignored += 1
            continue
        changes = _as_list(entry.get("changes"))
        if "changes" in entry and not changes:
            ignored += 1
        for change in changes:
            if not isinstance(change, dict):
                ignored += 1
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                ignored += 1
                continue
            messages = _as_list(value.get("messages"))
            if "messages" in value and not messages:
                ignored += 1
            for message in messages:
                if not isinstance(message, dict):
                    ignored += 1
                    continue
                msg_id = str(message.get("id") or "")
                from_number = str(message.get("from") or "")
                if not msg_id or not from_number:
                    ignored += 1
                    continue
                msg_text = _message_text(message)
                events.append(
                    ContextEvent(
                        id=f"whatsapp-{msg_id}",
                        source="whatsapp",
                        kind="message",
                        title=f"WhatsApp from {from_number}",
                        body=msg_text,
                        participants=[from_number],
                        importance=0.75,
                        metadata={"message_id": msg_id, "from": from_number},
                    )
                )
    return events, ignored
