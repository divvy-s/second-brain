from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None  # type: ignore[assignment]


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 1000


@dataclass(frozen=True)
class LLMResponse:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMUnavailable(Exception):
    pass


class LLMAdapter:
    """Standardized asynchronous interface for interacting with LLM providers."""

    def __init__(self, config: dict[str, Any], client_factory: Any | None = None) -> None:
        self.config = config
        llm_conf = config.get("llm", {})
        
        # Original logic used primary and fallback in DEFAULT_PROVIDERS, we simplify to primary for now 
        # or grab from the config. 
        self.provider = str(llm_conf.get("primary", {}).get("provider", "openai"))
        self.model = str(llm_conf.get("primary", {}).get("model", "gpt-4o-mini"))
        self.api_key = str(llm_conf.get("primary", {}).get("api_key", ""))
        self.base_url = str(llm_conf.get("primary", {}).get("base_url", ""))

        # The previous code also fetched env vars dynamically. Let's do a simple env fallback for tests.
        import os
        if not self.api_key:
            self.api_key = os.environ.get("OPENAI_API_KEY", "")

        if client_factory:
            self._client = client_factory(api_key=self.api_key, base_url=self.base_url or None)
        elif self.api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url or None)
        else:
            self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute an asynchronous completion request against the configured LLM."""
        if not self._client:
            raise LLMUnavailable("LLM provider is not configured.")
        try:
            # We handle tool/structured outputs by instructing the model via system prompt
            # rather than strict function calling, for broader compatibility (OpenRouter/Ollama/xAI).
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=request.messages,  # type: ignore
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            content = str(response.choices[0].message.content or "")
            usage = response.usage
            return LLMResponse(
                content=content,
                metadata={
                    "model": self.model,
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                },
            )
        except Exception as exc:
            raise LLMUnavailable(f"LLM request failed: {exc}") from exc


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
            data = {"intents": [{"intent": "note", "confidence": 0.9, "fields": {"title": "Note", "text": "foo"}, "reasoning": "mock"}]}
            return _MockAsyncResponse(json.dumps(data))
        return _MockAsyncResponse(self.canned_response)


class _MockAsyncChat:
    def __init__(self, canned_response: str = "mocked response") -> None:
        self.completions = _MockAsyncCompletions(canned_response)


class MockAsyncOpenAI:
    def __init__(self, canned_response: str = "mocked response", **kwargs: Any) -> None:
        self.chat = _MockAsyncChat(canned_response)
