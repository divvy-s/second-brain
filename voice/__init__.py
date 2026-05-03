"""
Voice Layer — Plug-and-Play STT + TTS integration.

This package is intentionally decoupled from all core layers.
If this entire directory is deleted, the rest of Second Brain
continues to run without a single line of change elsewhere.

Usage:
    from voice import VoiceService
    svc = VoiceService(config)
    transcript = await svc.transcribe(file_id, bot_token)  # STT
    audio_key  = await svc.speak(text, priority_score)      # TTS
"""
from __future__ import annotations

from voice.stt import WhisperSTT
from voice.tts import ElevenLabsTTS

__all__ = ["VoiceService", "WhisperSTT", "ElevenLabsTTS"]


class VoiceService:
    """
    Single public interface for the voice layer.

    Both STT and TTS are fully optional — if their API keys are
    missing the methods return None and the caller must handle the
    fallback gracefully.
    """

    def __init__(self, config: dict) -> None:
        voice_cfg = config.get("voice", {})
        self._stt = WhisperSTT(voice_cfg)
        self._tts = ElevenLabsTTS(voice_cfg)

    async def transcribe(
        self,
        file_id: str,
        bot_token: str,
        *,
        chat_id: int | str | None = None,
    ) -> str | None:
        """
        Download and transcribe a Telegram voice file.

        Returns the transcript text on success, None on any failure.
        The caller is responsible for sending the user a text fallback
        message if None is returned.
        """
        return await self._stt.transcribe(file_id, bot_token)

    async def speak(
        self,
        text: str,
        *,
        priority_score: float = 0.0,
        force: bool = False,
    ) -> str | None:
        """
        Generate speech for the given text.

        Only runs when priority_score > threshold OR force=True.
        Returns a Redis cache key that can be fetched via GET /voice/audio/{key}.
        Returns None if TTS is skipped or fails — the caller continues normally.
        """
        return await self._tts.speak(text, priority_score=priority_score, force=force)

    async def serve_audio(self, cache_key: str) -> bytes | None:
        """Retrieve audio bytes from the Redis cache by key."""
        return await self._tts.fetch_audio(cache_key)
