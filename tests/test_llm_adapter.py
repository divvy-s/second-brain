from __future__ import annotations

import os
import unittest

from intelligence.llm_adapter import LLMAdapter, LLMProvider, LLMRequest


class FakeMessage:
    content = "fallback answer"


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletions:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def create(self, **_: object) -> FakeResponse:
        if self.provider.provider == "xai":
            raise RuntimeError("rate limit")
        return FakeResponse()


class FakeChat:
    def __init__(self, provider: LLMProvider) -> None:
        self.completions = FakeCompletions(provider)


class FakeClient:
    def __init__(self, provider: LLMProvider) -> None:
        self.chat = FakeChat(provider)


class LLMAdapterTests(unittest.TestCase):
    def test_falls_back_from_grok_to_openrouter(self) -> None:
        config = {
            "llm": {
                "primary": {
                    "provider": "xai",
                    "base_url": "https://api.x.ai/v1",
                    "api_key_env": "XAI_API_KEY",
                    "model": "grok-4",
                },
                "fallback": {
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "model": "openai/gpt-4.1-mini",
                },
            }
        }
        old_xai = os.environ.get("XAI_API_KEY")
        old_router = os.environ.get("OPENROUTER_API_KEY")
        os.environ["XAI_API_KEY"] = "xai-test"
        os.environ["OPENROUTER_API_KEY"] = "router-test"
        try:
            adapter = LLMAdapter(config, client_factory=lambda provider, _: FakeClient(provider))
            response = adapter.complete(LLMRequest(messages=[{"role": "user", "content": "hello"}]))
            self.assertTrue(response.fallback_used)
            self.assertEqual(response.provider, "openrouter")
            self.assertEqual(response.content, "fallback answer")
        finally:
            if old_xai is None:
                os.environ.pop("XAI_API_KEY", None)
            else:
                os.environ["XAI_API_KEY"] = old_xai
            if old_router is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = old_router


if __name__ == "__main__":
    unittest.main()

