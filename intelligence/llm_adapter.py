from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None  # type: ignore[assignment]


SUPPORTED_PROVIDERS = {
    "openai",
    "gemini",
    "openrouter",
    "xai",
    "openai-compatible",
    "custom",
}

PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "openai/gpt-4.1-mini",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "model": "grok-4",
    },
}


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 1000


@dataclass(frozen=True)
class LLMResponse:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMStatus:
    configured: bool
    provider: str
    model: str
    base_url_configured: bool
    api_key_env: str
    error_code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": self.provider,
            "model": self.model,
            "base_url_configured": self.base_url_configured,
            "api_key_env": self.api_key_env,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class _ProviderConfig:
    provider: str
    base_url: str
    api_key_env: str
    api_key: str
    model: str
    timeout_seconds: float
    max_retries: int


class LLMUnavailable(Exception):
    def __init__(self, message: str, *, code: str = "llm_unavailable", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class LLMConfigurationError(LLMUnavailable):
    pass


def _llm_section(config: dict[str, Any]) -> dict[str, Any]:
    """Return the modern LLM section, accepting the old intelligence.llm shape."""
    llm_conf = config.get("llm")
    if isinstance(llm_conf, dict):
        return llm_conf
    legacy = config.get("intelligence", {}).get("llm") if isinstance(config.get("intelligence"), dict) else None
    if isinstance(legacy, dict):
        return {"primary": legacy}
    return {}


def _stripped(value: Any) -> str:
    return str(value or "").strip()


def _sanitize_error(raw: Any, secret: str = "") -> str:
    text = str(raw or "").strip() or "request failed"
    if secret:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"([?&](?:key|token|secret|password)=)[^&\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "[url]", text)
    return text[:220]


class LLMAdapter:
    """OpenAI-compatible async LLM adapter with explicit provider validation."""

    def __init__(self, config: dict[str, Any], client_factory: Any | None = None) -> None:
        self.config = config
        self._client = None
        self._client_factory = client_factory
        self._provider_config = self._resolve_provider_config(config)
        self._configuration_error: LLMConfigurationError | None = None

        if self._provider_config is None:
            return

        validation_error = self._validate_provider_config(self._provider_config)
        if validation_error is not None:
            self._configuration_error = validation_error
            return

        if client_factory is not None:
            self._client = self._build_client(client_factory)
            return

        if AsyncOpenAI is None:
            self._configuration_error = LLMConfigurationError(
                "The openai package is not installed, so the configured LLM provider cannot be used.",
                code="sdk_missing",
            )
            return

        self._client = AsyncOpenAI(
            api_key=self._provider_config.api_key,
            base_url=self._provider_config.base_url or None,
            timeout=self._provider_config.timeout_seconds,
            max_retries=0,
        )

    @property
    def provider(self) -> str:
        return self._provider_config.provider if self._provider_config else ""

    @property
    def model(self) -> str:
        return self._provider_config.model if self._provider_config else ""

    @property
    def api_key_env(self) -> str:
        return self._provider_config.api_key_env if self._provider_config else ""

    @property
    def configuration_error(self) -> LLMConfigurationError | None:
        return self._configuration_error

    def requires_configuration(self) -> bool:
        return self._provider_config is not None

    def is_configured(self) -> bool:
        return self._client is not None and self._configuration_error is None

    def status(self) -> LLMStatus:
        if self._provider_config is None:
            return LLMStatus(
                configured=False,
                provider="",
                model="",
                base_url_configured=False,
                api_key_env="",
                error_code="not_configured",
                message="LLM provider is not configured.",
            )
        if self._configuration_error is not None:
            return LLMStatus(
                configured=False,
                provider=self._provider_config.provider,
                model=self._provider_config.model,
                base_url_configured=bool(self._provider_config.base_url),
                api_key_env=self._provider_config.api_key_env,
                error_code=self._configuration_error.code,
                message=self._configuration_error.message,
            )
        return LLMStatus(
            configured=True,
            provider=self._provider_config.provider,
            model=self._provider_config.model,
            base_url_configured=bool(self._provider_config.base_url),
            api_key_env=self._provider_config.api_key_env,
            message="LLM provider is configured.",
        )

    def validate_startup(self, *, strict: bool) -> None:
        if self._configuration_error is not None and strict:
            raise self._configuration_error

    def ensure_available(self) -> None:
        if self.is_configured():
            return
        if self._configuration_error is not None:
            raise self._configuration_error
        raise LLMUnavailable("LLM provider is not configured.", code="not_configured")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute a chat completion request with bounded timeout and one retry by default."""
        self.ensure_available()
        assert self._client is not None
        assert self._provider_config is not None

        attempts = max(1, self._provider_config.max_retries + 1)
        last_error: LLMUnavailable | None = None
        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._provider_config.model,
                        messages=request.messages,  # type: ignore[arg-type]
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                    ),
                    timeout=self._provider_config.timeout_seconds + 1,
                )
                content = str(response.choices[0].message.content or "")
                usage = response.usage
                return LLMResponse(
                    content=content,
                    metadata={
                        "provider": self._provider_config.provider,
                        "model": self._provider_config.model,
                        "prompt_tokens": usage.prompt_tokens if usage else 0,
                        "completion_tokens": usage.completion_tokens if usage else 0,
                    },
                )
            except Exception as exc:
                failure = self._classify_exception(exc)
                last_error = failure
                if not failure.retryable or attempt >= attempts - 1:
                    raise failure from exc
                await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))

        raise last_error or LLMUnavailable("LLM request failed.", code="request_failed")

    def _resolve_provider_config(self, config: dict[str, Any]) -> _ProviderConfig | None:
        llm_conf = _llm_section(config)
        primary = llm_conf.get("primary")
        if not isinstance(primary, dict):
            return None

        provider = _stripped(primary.get("provider")).lower()
        defaults = PROVIDER_DEFAULTS.get(provider, {})
        api_key_env = _stripped(primary.get("api_key_env") or defaults.get("api_key_env"))
        direct_api_key = _stripped(primary.get("api_key"))
        api_key = direct_api_key or (_stripped(os.environ.get(api_key_env)) if api_key_env else "")

        timeout_seconds = primary.get("timeout_seconds", llm_conf.get("timeout_seconds", 25))
        max_retries = primary.get("max_retries", llm_conf.get("max_retries", 1))
        try:
            timeout = max(1.0, float(timeout_seconds))
        except (TypeError, ValueError):
            timeout = 25.0
        try:
            retries = max(0, int(max_retries))
        except (TypeError, ValueError):
            retries = 1

        return _ProviderConfig(
            provider=provider,
            base_url=_stripped(primary.get("base_url") or defaults.get("base_url")),
            api_key_env=api_key_env,
            api_key=api_key,
            model=_stripped(primary.get("model") or defaults.get("model")),
            timeout_seconds=timeout,
            max_retries=retries,
        )

    def _validate_provider_config(self, provider_config: _ProviderConfig) -> LLMConfigurationError | None:
        if not provider_config.provider:
            return LLMConfigurationError("LLM primary provider is missing.", code="invalid_provider")
        if provider_config.provider not in SUPPORTED_PROVIDERS:
            return LLMConfigurationError(
                f"Unsupported LLM provider '{provider_config.provider}'. Use gemini, openai, or openai-compatible.",
                code="invalid_provider",
            )
        if provider_config.provider in {"openai-compatible", "custom"} and not provider_config.base_url:
            return LLMConfigurationError(
                "OpenAI-compatible LLM providers require llm.primary.base_url.",
                code="missing_base_url",
            )
        if not provider_config.model:
            return LLMConfigurationError("LLM model is missing at llm.primary.model.", code="missing_model")
        if not provider_config.api_key:
            env_hint = provider_config.api_key_env or "the configured api_key_env"
            return LLMConfigurationError(
                f"LLM API key is missing. Set {env_hint} for provider '{provider_config.provider}'.",
                code="missing_api_key",
            )
        return None

    def _build_client(self, client_factory: Any) -> Any:
        assert self._provider_config is not None
        kwargs = {
            "api_key": self._provider_config.api_key,
            "base_url": self._provider_config.base_url or None,
            "timeout": self._provider_config.timeout_seconds,
            "max_retries": 0,
        }
        try:
            return client_factory(**kwargs)
        except TypeError:
            try:
                return client_factory(api_key=kwargs["api_key"], base_url=kwargs["base_url"])
            except TypeError:
                return client_factory()

    def _classify_exception(self, exc: Exception) -> LLMUnavailable:
        assert self._provider_config is not None
        name = type(exc).__name__.lower()
        safe = _sanitize_error(exc, self._provider_config.api_key)
        lowered = safe.lower()
        provider = self._provider_config.provider or "LLM provider"

        if isinstance(exc, asyncio.TimeoutError) or "timeout" in name or "timed out" in lowered:
            return LLMUnavailable(
                f"{provider} timed out. Please try again in a moment.",
                code="provider_timeout",
                retryable=True,
            )
        if (
            "authentication" in name
            or "permission" in name
            or "unauthorized" in lowered
            or "invalid api key" in lowered
            or "incorrect api key" in lowered
            or "http 401" in lowered
            or "status code: 401" in lowered
        ):
            env_hint = self._provider_config.api_key_env or "the configured API key"
            return LLMUnavailable(
                f"LLM authentication failed. Check {env_hint} for provider '{provider}'.",
                code="authentication_failed",
                retryable=False,
            )
        if "rate" in lowered and "limit" in lowered or "http 429" in lowered or "status code: 429" in lowered:
            return LLMUnavailable(
                f"{provider} is rate limited. Please wait and try again.",
                code="rate_limited",
                retryable=False,
            )
        if "connection" in name or "connection" in lowered or "temporarily unavailable" in lowered:
            return LLMUnavailable(
                f"{provider} is unreachable right now. Please check provider connectivity.",
                code="provider_unreachable",
                retryable=True,
            )
        if re.search(r"\b(500|502|503|504)\b", lowered):
            return LLMUnavailable(
                f"{provider} returned a temporary server error. Please try again.",
                code="provider_unreachable",
                retryable=True,
            )
        return LLMUnavailable(f"LLM request failed: {safe}", code="request_failed", retryable=False)


# ---------------------------------------------------------------------------
# Mock client for testing
# ---------------------------------------------------------------------------
class _MockAsyncMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockAsyncChoice:
    def __init__(self, content: str) -> None:
        self.message = _MockAsyncMessage(content)


class _MockAsyncUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 20


class _MockAsyncResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_MockAsyncChoice(content)]
        self.usage = _MockAsyncUsage()


class _MockAsyncCompletions:
    def __init__(self, canned_response: str = "mocked response") -> None:
        self.canned_response = canned_response

    async def create(self, **kwargs: Any) -> _MockAsyncResponse:
        messages = kwargs.get("messages", [])
        if any("decompose" in str(m.get("content", "")).lower() for m in messages):
            data = {"steps": [{"title": "Do something", "owner": "task_agent", "action": {"type": "create_task"}}]}
            return _MockAsyncResponse(json.dumps(data))
        if any("intents" in str(m.get("content", "")).lower() for m in messages):
            data = {
                "intents": [
                    {
                        "intent": "note",
                        "confidence": 0.9,
                        "fields": {"title": "Note", "text": "foo"},
                        "reasoning": "mock",
                    }
                ]
            }
            return _MockAsyncResponse(json.dumps(data))
        return _MockAsyncResponse(self.canned_response)


class _MockAsyncChat:
    def __init__(self, canned_response: str = "mocked response") -> None:
        self.completions = _MockAsyncCompletions(canned_response)


class MockAsyncOpenAI:
    def __init__(self, canned_response: str = "mocked response", **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat = _MockAsyncChat(canned_response)
