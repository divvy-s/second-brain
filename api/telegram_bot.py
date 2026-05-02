from __future__ import annotations

import re
from typing import Any

from api.dependencies import AppServices
from api.dependencies import build_workflow
from connectors.base import ContextEvent


class TelegramApprovalBot:
    def __init__(self, services: AppServices) -> None:
        self.services = services

    def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text") or "").strip()
        if text:
            approval = self._handle_text(text)
            if approval:
                return approval
            event = ContextEvent(
                source="mcp_telegram",
                kind="message",
                title="Telegram capture",
                body=text,
                participants=[str(message.get("from", {}).get("username") or message.get("from", {}).get("id") or "")],
                metadata={"chat_id": message.get("chat", {}).get("id"), "update_id": update.get("update_id")},
            )
            stored = build_workflow(self.services).ingest({"events": [event]})["events"][0]
            return {"status": "captured", "event": stored.to_dict()}
        voice = message.get("voice")
        if voice:
            event = ContextEvent(
                source="mcp_telegram",
                kind="voice",
                title="Telegram voice note",
                body=f"Telegram voice file {voice.get('file_id')} received for later processing.",
                participants=[str(message.get("from", {}).get("username") or message.get("from", {}).get("id") or "")],
                metadata={"voice": voice, "chat_id": message.get("chat", {}).get("id")},
            )
            stored = build_workflow(self.services).ingest({"events": [event]})["events"][0]
            return {"status": "voice_captured", "event": stored.to_dict()}
        return {"status": "ignored"}

    def _handle_text(self, text: str) -> dict[str, Any] | None:
        approve = re.match(r"^/approve\s+([0-9a-fA-F-]{8,})", text)
        if approve:
            request = self.services.approval_gate.approve(approve.group(1))
            execution = self.services.executor.execute_approved(request.id)
            return {"status": "approved", "request": request.__dict__, "execution": execution}
        reject = re.match(r"^/reject\s+([0-9a-fA-F-]{8,})", text)
        if reject:
            request = self.services.approval_gate.reject(reject.group(1))
            return {"status": "rejected", "request": request.__dict__}
        return None
