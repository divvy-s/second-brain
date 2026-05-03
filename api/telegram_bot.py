from __future__ import annotations

import logging
import re
from typing import Any

from api.dependencies import AppServices
from api.dependencies import build_workflow
from connectors.base import ContextEvent

logger = logging.getLogger(__name__)

# Lazy-import the voice layer so deleting `voice/` doesn't break anything
try:
    from voice import VoiceService  # type: ignore[import]
    _VOICE_AVAILABLE = True
except ImportError:
    _VOICE_AVAILABLE = False
    logger.debug("Voice layer not installed; Telegram voice notes will be stored as stubs.")

_VOICE_FALLBACK_MSG = "🎤 Audio processing failed; please type your command."


class TelegramApprovalBot:
    def __init__(self, services: AppServices) -> None:
        self.services = services
        self._voice: Any = None  # lazily built per request

    def _get_voice_service(self) -> Any:
        if not _VOICE_AVAILABLE:
            return None
        if self._voice is None:
            self._voice = VoiceService(self.services.config)
        return self._voice

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(update, dict):
            return {"status": "ignored", "reason": "Malformed Telegram update"}
        message = update.get("message") or update.get("edited_message") or {}
        if not isinstance(message, dict):
            return {"status": "ignored", "reason": "No Telegram message payload"}

        # ── Text messages ────────────────────────────────────────────────
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
            stored = (await build_workflow(self.services).ingest_lightweight({"events": [event]}))["events"][0]
            return {"status": "captured", "event": stored.to_dict()}

        # ── Voice messages (Ghost Input Logic) ───────────────────────────
        voice = message.get("voice")
        if voice:
            return await self._handle_voice(message, voice, update)

        return {"status": "ignored"}

    async def _handle_voice(
        self,
        message: dict[str, Any],
        voice: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Intercept Telegram voice notes BEFORE the Orchestration Layer.

        1. Transcribe via Whisper.
        2. On success → inject transcript into Brain Dump pipeline (identical to typed text).
        3. On failure → store stub event and return a text fallback notice.
        """
        file_id: str = voice.get("file_id", "")
        chat_id = message.get("chat", {}).get("id")
        sender = str(message.get("from", {}).get("username") or message.get("from", {}).get("id") or "")

        # Shared metadata for both success and fallback paths
        voice_meta = {
            "voice": voice,
            "chat_id": chat_id,
            "update_id": update.get("update_id"),
            "duration_secs": voice.get("duration"),
        }

        voice_svc = self._get_voice_service()
        transcript: str | None = None

        if voice_svc and file_id:
            bot_token_cfg = self.services.config.get("plugins", {}).get("telegram", {})
            bot_token_env = bot_token_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
            import os
            bot_token = os.environ.get(bot_token_env, "").strip()
            if bot_token:
                transcript = await voice_svc.transcribe(file_id, bot_token, chat_id=chat_id)

        if transcript:
            # ✅ SUCCESS PATH — treat transcript exactly like a typed brain dump
            voice_meta["transcript_source"] = "whisper"
            event = ContextEvent(
                source="mcp_telegram",
                kind="brain_dump",           # ← treated as a first-class brain dump
                title="Voice: " + transcript[:60],
                body=transcript,
                participants=[sender],
                metadata=voice_meta,
            )
            stored = (await build_workflow(self.services).ingest_lightweight({"events": [event]}))["events"][0]
            logger.info("Voice transcript stored as brain_dump event %s", stored.id)
            return {
                "status": "voice_transcribed",
                "transcript": transcript,
                "event": stored.to_dict(),
            }

        # ❌ FAILURE PATH — store stub event so the voice note isn't lost,
        #    and notify the user to type instead.
        logger.warning("Voice transcription unavailable for file_id=%s", file_id)
        stub_event = ContextEvent(
            source="mcp_telegram",
            kind="voice",
            title="Telegram voice note (untranscribed)",
            body=_VOICE_FALLBACK_MSG,
            participants=[sender],
            metadata={**voice_meta, "transcript_source": "none"},
        )
        stored_stub = (
            await build_workflow(self.services).ingest_lightweight({"events": [stub_event]})
        )["events"][0]
        return {
            "status": "voice_fallback",
            "message": _VOICE_FALLBACK_MSG,
            "event": stored_stub.to_dict(),
        }

    def _handle_text(self, text: str) -> dict[str, Any] | None:
        approve = re.match(r"^/approve\s+([0-9a-fA-F-]{8,})", text)
        if approve:
            try:
                request = self.services.approval_gate.approve(approve.group(1))
            except KeyError:
                return {"status": "approval_not_found", "request_id": approve.group(1)}
            try:
                execution = self.services.executor.execute_approved(request.id)
                return {"status": "approved", "request": request.__dict__, "execution": execution}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        reject = re.match(r"^/reject\s+([0-9a-fA-F-]{8,})", text)
        if reject:
            try:
                request = self.services.approval_gate.reject(reject.group(1))
                return {"status": "rejected", "request": request.__dict__}
            except KeyError:
                return {"status": "approval_not_found", "request_id": reject.group(1)}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return None
