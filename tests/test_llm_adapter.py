from __future__ import annotations

import pytest

from intelligence.llm_adapter import LLMAdapter, LLMRequest, MockAsyncOpenAI


@pytest.mark.asyncio
async def test_llm_adapter_configured():
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
    assert adapter.is_configured()


@pytest.mark.asyncio
async def test_llm_adapter_complete():
    config = {
        "intelligence": {
            "llm": {
                "api_key": "test_key",
            }
        }
    }
    adapter = LLMAdapter(config, client_factory=MockAsyncOpenAI)
    response = await adapter.complete(LLMRequest(messages=[{"role": "user", "content": "hello"}]))
    assert response.content == "mocked response"
    assert response.metadata["model"] == "gpt-4o-mini"
