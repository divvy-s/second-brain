"""
Speech-to-Text using OpenAI Whisper.

Ghost Input Logic:
  1. Download the audio file from Telegram using getFile + bot token.
  2. Send the audio bytes to the Whisper API.
  3. Return the raw transcript text (or None on any failure).

Zero changes are required in the downstream Brain Dump pipeline —
the transcript is injected identically to typed text.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_WHISPER_MODEL = "whisper-1"
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 30.0  # Whisper can take a moment for long audio


class WhisperSTT:
    def __init__(self, voice_config: dict) -> None:
        self._cfg = voice_config
        # Reuse the existing OPENAI_API_KEY — no new credential needed.
        api_key_env = voice_config.get("whisper_api_key_env", "OPENAI_API_KEY")
        self._api_key = os.environ.get(api_key_env, "").strip()

    @property
    def _is_configured(self) -> bool:
        return bool(self._api_key)

    async def transcribe(self, file_id: str, bot_token: str) -> str | None:
        """
        Download a Telegram voice file and transcribe it via Whisper.

        Returns the transcript string on success, None on any failure.
        This method NEVER raises — all errors are caught and logged.
        """
        if not self._is_configured:
            logger.info("Whisper STT: OPENAI_API_KEY not configured; skipping transcription.")
            return None
        try:
            audio_bytes = await self._download_telegram_voice(file_id, bot_token)
            if audio_bytes is None:
                return None
            return await self._whisper_transcribe(audio_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Whisper STT transcription failed: %s", exc)
            return None

    async def _download_telegram_voice(self, file_id: str, bot_token: str) -> bytes | None:
        """Get the file path from Telegram, then download the OGG audio."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx is required for voice downloads (pip install httpx)")
            return None

        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                # Step 1: resolve file_id → file_path
                get_file_url = f"{_TELEGRAM_API}/bot{bot_token}/getFile?file_id={file_id}"
                resp = await client.get(get_file_url)
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                if not data.get("ok"):
                    logger.warning("Telegram getFile failed: %s", data)
                    return None
                file_path = data["result"]["file_path"]

                # Step 2: download the actual audio bytes
                download_url = f"{_TELEGRAM_API}/file/bot{bot_token}/{file_path}"
                audio_resp = await client.get(download_url, timeout=_READ_TIMEOUT)
                audio_resp.raise_for_status()
                return audio_resp.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to download Telegram voice file: %s", exc)
            return None

    async def _whisper_transcribe(self, audio_bytes: bytes) -> str | None:
        """Send audio bytes to the OpenAI Whisper API and return the transcript."""
        try:
            import httpx
        except ImportError:
            return None

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"  # Whisper needs a filename with extension

        try:
            async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                    data={"model": _WHISPER_MODEL},
                )
                resp.raise_for_status()
                result = resp.json()
                transcript = str(result.get("text", "")).strip()
                if not transcript:
                    logger.info("Whisper returned an empty transcript.")
                    return None
                logger.info("Whisper transcript: %r (len=%d)", transcript[:80], len(transcript))
                return transcript
        except Exception as exc:  # noqa: BLE001
            logger.warning("Whisper API call failed: %s", exc)
            return None
