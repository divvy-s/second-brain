"""
Text-to-Speech using ElevenLabs.

Non-Blocking Output Logic:
  - Called from FastAPI BackgroundTasks AFTER the primary response is sent.
  - System never waits for this.
  - Priority: only runs for priority_score > threshold OR force=True.
  - Circuit Breaker: 3 consecutive failures → 30-min cooldown (never permanent).
  - Audio stored in Redis with a 5-min TTL — NOT in SQLite/ChromaDB.
  - Falls back silently on: missing key, timeout, Redis unavailability.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
_TTS_TIMEOUT_SECONDS = 1.5  # Hard limit as specified
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel" — a safe default
_CIRCUIT_BREAKER_THRESHOLD = 3
_REDIS_TTL_SECONDS = 300  # 5 minutes
_REDIS_CIRCUIT_KEY = "voice:circuit_breaker"
_REDIS_COOLDOWN_KEY = "voice:circuit_cooldown_until"
_REDIS_FAIL_COUNT_KEY = "voice:circuit_fail_count"


class ElevenLabsTTS:
    def __init__(self, voice_config: dict) -> None:
        self._cfg = voice_config
        api_key_env = voice_config.get("elevenlabs_api_key_env", "ELEVENLABS_API_KEY")
        voice_id_env = voice_config.get("elevenlabs_voice_id_env", "ELEVENLABS_VOICE_ID")
        self._api_key = os.environ.get(api_key_env, "").strip()
        self._voice_id = os.environ.get(voice_id_env, "").strip() or _DEFAULT_VOICE_ID
        self._threshold = float(voice_config.get("tts_priority_threshold", 0.80))
        self._audio_ttl = int(voice_config.get("audio_ttl_seconds", _REDIS_TTL_SECONDS))
        self._reset_minutes = int(voice_config.get("circuit_breaker_reset_minutes", 30))
        self._redis: Any = None  # lazy init

    @property
    def _is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_redis(self) -> Any:
        """Lazy-initialise Redis. Returns None if Redis is unavailable."""
        if self._redis is not None:
            return self._redis
        try:
            import redis as redis_lib

            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            client = redis_lib.from_url(url, decode_responses=False)
            client.ping()
            self._redis = client
            return self._redis
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis unavailable for TTS cache: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    #  Circuit Breaker (Redis-backed, 30-min auto-reset, never permanent) #
    # ------------------------------------------------------------------ #

    def _is_circuit_open(self) -> bool:
        """Returns True if in cooldown (i.e., calls should be skipped)."""
        r = self._get_redis()
        if r is None:
            # No Redis → no circuit state → always try
            return False
        try:
            cooldown_until_raw = r.get(_REDIS_COOLDOWN_KEY)
            if cooldown_until_raw is None:
                return False
            cooldown_until = float(cooldown_until_raw)
            if time.time() < cooldown_until:
                remaining = int(cooldown_until - time.time())
                logger.info("TTS circuit open — cooldown %ds remaining", remaining)
                return True
            # Cooldown expired → auto-reset
            r.delete(_REDIS_COOLDOWN_KEY)
            r.set(_REDIS_FAIL_COUNT_KEY, 0)
            logger.info("TTS circuit breaker auto-reset after cooldown.")
            return False
        except Exception:  # noqa: BLE001
            return False

    def _record_failure(self) -> None:
        """Record a TTS failure. Opens the circuit after threshold failures."""
        r = self._get_redis()
        if r is None:
            return
        try:
            new_count = r.incr(_REDIS_FAIL_COUNT_KEY)
            if int(new_count) >= _CIRCUIT_BREAKER_THRESHOLD:
                cooldown_until = time.time() + (self._reset_minutes * 60)
                r.set(_REDIS_COOLDOWN_KEY, str(cooldown_until))
                r.set(_REDIS_FAIL_COUNT_KEY, 0)
                logger.warning(
                    "TTS circuit breaker opened after %d failures — "
                    "cooldown for %d minutes, then auto-reset.",
                    _CIRCUIT_BREAKER_THRESHOLD,
                    self._reset_minutes,
                )
        except Exception:  # noqa: BLE001
            pass

    def _record_success(self) -> None:
        """Reset failure count on a successful TTS call."""
        r = self._get_redis()
        if r is None:
            return
        try:
            r.set(_REDIS_FAIL_COUNT_KEY, 0)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    #  Redis Audio Cache                                                   #
    # ------------------------------------------------------------------ #

    def _cache_key(self, text: str) -> str:
        h = hashlib.sha256(f"{self._voice_id}:{text}".encode()).hexdigest()[:16]
        return f"tts_audio:{h}"

    def _store_audio(self, cache_key: str, audio_bytes: bytes) -> None:
        r = self._get_redis()
        if r is None:
            return
        try:
            r.setex(cache_key, self._audio_ttl, audio_bytes)
            logger.info("TTS audio cached at key=%s ttl=%ds", cache_key, self._audio_ttl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to cache TTS audio: %s", exc)

    async def fetch_audio(self, cache_key: str) -> bytes | None:
        """Retrieve audio bytes from Redis cache."""
        r = self._get_redis()
        if r is None:
            return None
        try:
            data = r.get(cache_key)
            return bytes(data) if data else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    #  Main speak() entry point                                            #
    # ------------------------------------------------------------------ #

    async def speak(self, text: str, *, priority_score: float = 0.0, force: bool = False) -> str | None:
        """
        Generate speech for the given text and store in Redis.

        Returns the Redis cache key on success, None if skipped or failed.
        This method NEVER raises.
        """
        if not self._is_configured:
            logger.debug("TTS: ELEVENLABS_API_KEY not set; skipping.")
            return None

        if not force and priority_score < self._threshold:
            logger.debug("TTS: priority_score=%.2f < threshold=%.2f; skipping.", priority_score, self._threshold)
            return None

        if self._is_circuit_open():
            return None

        cache_key = self._cache_key(text)

        # Check cache first — avoid redundant API calls
        r = self._get_redis()
        if r is not None:
            try:
                existing = r.exists(cache_key)
                if existing:
                    logger.info("TTS cache hit for key=%s", cache_key)
                    return cache_key
            except Exception:  # noqa: BLE001
                pass

        audio_bytes = await self._call_elevenlabs(text)
        if audio_bytes is None:
            return None

        self._store_audio(cache_key, audio_bytes)
        self._record_success()
        return cache_key

    async def _call_elevenlabs(self, text: str) -> bytes | None:
        """Call the ElevenLabs TTS endpoint with a hard 1.5-second timeout."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx is required for ElevenLabs TTS (pip install httpx)")
            return None

        url = f"{_ELEVENLABS_TTS_URL}/{self._voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text[:2500],  # ElevenLabs has a character limit
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }

        try:
            async with httpx.AsyncClient(timeout=_TTS_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                audio = resp.content
                if not audio:
                    logger.warning("ElevenLabs returned empty audio bytes.")
                    self._record_failure()
                    return None
                logger.info("ElevenLabs TTS generated %d bytes.", len(audio))
                return audio
        except Exception as exc:  # noqa: BLE001
            logger.warning("ElevenLabs TTS failed: %s", type(exc).__name__)
            self._record_failure()
            return None
