import pytest
from src.llm_client import LLMClient


class TestLLMClient:
    def test_init_defaults(self):
        client = LLMClient()
        assert client.temperature == 0.3
        assert client.timeout == 150.0
        assert "/chat/completions" in client.completions_url

    def test_init_custom_url(self):
        client = LLMClient(url="http://localhost:1234/v1/chat/completions")
        assert client.base_url == "http://localhost:1234/v1"
        assert client.completions_url == "http://localhost:1234/v1/chat/completions"

    def test_init_custom_params(self):
        client = LLMClient(timeout=60.0, model="qwen2.5", temperature=0.1)
        assert client.timeout == 60.0
        assert client.model == "qwen2.5"
        assert client.temperature == 0.1

    def test_no_client_returns_error(self):
        client = LLMClient()

        async def test():
            result = await client.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

        import asyncio
        asyncio.run(test())

    def test_detect_model_no_client(self):
        client = LLMClient()

        async def test():
            result = await client.detect_model()
            assert result is None

        import asyncio
        asyncio.run(test())

    def test_context_manager(self):
        async def test():
            async with LLMClient() as client:
                assert client._client is not None
            assert client._client is None

        import asyncio
        asyncio.run(test())

    def test_chat_fails_fast_port(self):
        client = LLMClient(url="http://localhost:1/v1/chat/completions", timeout=1)

        async def test():
            async with client:
                result = await client.chat([{"role": "user", "content": "hi"}])
                assert "error" in result

        import asyncio
        asyncio.run(test())
