from __future__ import annotations

import asyncio
import unittest

from intelligence.llm_adapter import LLMAdapter, LLMRequest, MockAsyncOpenAI


class LLMAdapterTests(unittest.TestCase):
    def test_llm_adapter_configured(self) -> None:
        config = {
            "intelligence": {
                "llm": {
                    "provider": "test_provider",
                    "api_key": "test_key",
                    "base_url": "http://test",
                    "model": "test-model",
                }
            }
        }
        adapter = LLMAdapter(config, client_factory=MockAsyncOpenAI)
        self.assertTrue(adapter.is_configured())

    def test_llm_adapter_complete(self) -> None:
        config = {
            "intelligence": {
                "llm": {
                    "api_key": "test_key",
                }
            }
        }
        adapter = LLMAdapter(config, client_factory=MockAsyncOpenAI)
        response = asyncio.run(adapter.complete(LLMRequest(messages=[{"role": "user", "content": "hello"}])))
        self.assertEqual(response.content, "mocked response")
        self.assertEqual(response.metadata["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
