from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMUnavailable, MockAsyncOpenAI


def llm_config(provider: str = "gemini", api_key_env: str = "GEMINI_API_KEY") -> dict:
    return {
        "llm": {
            "primary": {
                "provider": provider,
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "api_key_env": api_key_env,
                "model": "gemini-2.5-flash",
                "timeout_seconds": 2,
                "max_retries": 1,
            }
        }
    }


class LLMAdapterTests(unittest.TestCase):
    def test_llm_adapter_reads_provider_config_from_env(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            adapter = LLMAdapter(llm_config(), client_factory=MockAsyncOpenAI)
        self.assertTrue(adapter.is_configured())
        self.assertEqual(adapter.provider, "gemini")
        self.assertEqual(adapter.model, "gemini-2.5-flash")
        self.assertEqual(adapter.api_key_env, "GEMINI_API_KEY")
        self.assertTrue(adapter.status().base_url_configured)

    def test_openai_provider_defaults_are_supported(self) -> None:
        config = {"llm": {"primary": {"provider": "openai", "api_key_env": "OPENAI_API_KEY", "model": "gpt-4o-mini"}}}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "openai-key"}, clear=False):
            adapter = LLMAdapter(config, client_factory=MockAsyncOpenAI)
        self.assertTrue(adapter.is_configured())
        self.assertEqual(adapter.status().provider, "openai")

    def test_invalid_provider_configuration_is_reported(self) -> None:
        adapter = LLMAdapter(llm_config(provider="unknown-ai"), client_factory=MockAsyncOpenAI)
        self.assertFalse(adapter.is_configured())
        self.assertEqual(adapter.status().error_code, "invalid_provider")
        with self.assertRaisesRegex(LLMUnavailable, "Unsupported LLM provider"):
            adapter.ensure_available()

    def test_missing_api_key_names_required_env_var(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            adapter = LLMAdapter(llm_config(api_key_env="GEMINI_API_KEY"), client_factory=MockAsyncOpenAI)
        self.assertFalse(adapter.is_configured())
        self.assertEqual(adapter.status().error_code, "missing_api_key")
        self.assertIn("GEMINI_API_KEY", adapter.status().message)

    def test_llm_adapter_complete(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            adapter = LLMAdapter(llm_config(), client_factory=MockAsyncOpenAI)
        response = asyncio.run(adapter.complete(LLMRequest(messages=[{"role": "user", "content": "hello"}])))
        self.assertEqual(response.content, "mocked response")
        self.assertEqual(response.metadata["model"], "gemini-2.5-flash")
        self.assertEqual(response.metadata["provider"], "gemini")


if __name__ == "__main__":
    unittest.main()
