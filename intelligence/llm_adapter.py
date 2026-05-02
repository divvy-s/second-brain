from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from connectors.base import redact


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMProvider:
    provider: str
    base_url: str
    api_key_env: str
    model: str


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, str]]
    temperature: float = 0.2
    max_tokens: int = 1200
    timeout_seconds: int = 45
    model: str | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    latency_ms: int
    fallback_used: bool


ClientFactory = Callable[[LLMProvider, str], Any]


class LLMAdapter:
    DEFAULT_PROVIDERS = [
        {
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key_env": "GEMINI_API_KEY",
            "model": "gemini-2.5-flash",
        },
        {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
            "model": "openai/gpt-4.1-mini",
        },
    ]

    def __init__(self, config: dict[str, Any], client_factory: ClientFactory | None = None) -> None:
        llm_config = config.get("llm", config)
        providers: list[LLMProvider] = []
        for key, fallback in zip(("primary", "fallback"), self.DEFAULT_PROVIDERS):
            raw_provider = llm_config.get(key) if isinstance(llm_config, dict) else None
            if not isinstance(raw_provider, dict):
                raw_provider = fallback
            providers.append(LLMProvider(**raw_provider))
        self.providers = providers
        self.client_factory = client_factory

    def _client(self, provider: LLMProvider, api_key: str) -> Any:
        if self.client_factory is not None:
            return self.client_factory(provider, api_key)
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise LLMUnavailable("openai package is required for LLM calls") from exc
        return OpenAI(api_key=api_key, base_url=provider.base_url)

    def is_configured(self) -> bool:
        return any(os.environ.get(provider.api_key_env) for provider in self.providers)

    def complete(self, request: LLMRequest) -> LLMResponse:
        errors: list[dict[str, str]] = []
        for index, provider in enumerate(self.providers):
            api_key = os.environ.get(provider.api_key_env, "")
            if not api_key:
                errors.append({"provider": provider.provider, "error": f"{provider.api_key_env} is not set"})
                continue
            model = request.model or provider.model
            started = time.perf_counter()
            try:
                response = self._client(provider, api_key).chat.completions.create(
                    model=model,
                    messages=request.messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    timeout=request.timeout_seconds,
                )
                content = response.choices[0].message.content or ""
                return LLMResponse(
                    content=content,
                    provider=provider.provider,
                    model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    fallback_used=index > 0,
                )
            except Exception as exc:
                errors.append({"provider": provider.provider, "error": str(exc)[:300]})
                continue
        raise LLMUnavailable(f"No LLM provider completed the request: {redact(errors)}")

