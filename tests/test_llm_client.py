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

    def test_usage_counted_on_success(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        client = LLMClient(model="test-model")

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}}

        client._client = Mock()
        client._client.post = AsyncMock(return_value=FakeResp())

        async def test():
            result = await client._try_chat("http://x/v1/chat/completions",
                                            [{"role": "user", "content": "hi"}])

        asyncio.run(test())
        assert client.prompt_tokens == 120
        assert client.completion_tokens == 45

        client.reset_usage()
        assert client.prompt_tokens == 0
        assert client.completion_tokens == 0

    def test_usage_ignored_on_error(self):
        import asyncio
        client = LLMClient(model="test-model")
        client._client = None

        async def test():
            result = await client.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

        asyncio.run(test())
        assert client.prompt_tokens == 0
        assert client.completion_tokens == 0

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

    def test_chat_passes_temperature_and_max_tokens(self):
        import asyncio
        client = LLMClient()

        captured = {}

        async def fake_try_chat(url, messages, tools, force_json, temperature, max_tokens):
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            return {"choices": [{"message": {"content": "ok"}}]}

        async def test():
            async with client:
                client._try_chat = fake_try_chat
                await client.chat([{"role": "user", "content": "hi"}],
                                  temperature=0.1, max_tokens=2048)

        asyncio.run(test())
        assert captured["temperature"] == 0.1
        assert captured["max_tokens"] == 2048

    def test_chat_uses_defaults_when_not_passed(self):
        import asyncio
        client = LLMClient(temperature=0.7)

        captured = {}

        async def fake_try_chat(url, messages, tools, force_json, temperature, max_tokens):
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            return {"choices": [{"message": {"content": "ok"}}]}

        async def test():
            async with client:
                client._try_chat = fake_try_chat
                await client.chat([{"role": "user", "content": "hi"}])

        asyncio.run(test())
        assert captured["temperature"] == 0.7
        assert captured["max_tokens"] == 8192

    def _fake_post(self, client, captured):
        from unittest.mock import Mock

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

        async def fake_post(url, json=None, headers=None, timeout=None):
            captured["headers"] = headers or {}
            return FakeResp()

        client._client = Mock()
        client._client.post = fake_post

    def test_auth_header_sent_when_api_key_set(self):
        import asyncio
        from unittest.mock import Mock
        client = LLMClient(model="m", api_key="sk-test-123")
        captured = {}
        self._fake_post(client, captured)

        async def run():
            await client._try_chat("http://x/v1/chat/completions", [{"role": "user", "content": "hi"}])

        asyncio.run(run())
        assert captured["headers"]["Authorization"] == "Bearer sk-test-123"

    def test_no_auth_header_without_api_key(self):
        import asyncio
        from unittest.mock import Mock
        client = LLMClient(model="m")
        captured = {}
        self._fake_post(client, captured)

        async def run():
            await client._try_chat("http://x/v1/chat/completions", [{"role": "user", "content": "hi"}])

        asyncio.run(run())
        assert "Authorization" not in captured["headers"]

    def test_custom_headers_merged_and_not_mutated(self):
        import asyncio
        from unittest.mock import Mock
        custom = {"User-Agent": "PricerVision/2.0"}
        client = LLMClient(model="m", api_key="sk-k", headers=custom)
        captured = {}
        self._fake_post(client, captured)

        async def run():
            await client._try_chat("http://x/v1/chat/completions", [{"role": "user", "content": "hi"}])

        asyncio.run(run())
        assert captured["headers"]["User-Agent"] == "PricerVision/2.0"
        assert captured["headers"]["Authorization"] == "Bearer sk-k"
        assert "Authorization" not in custom

    @staticmethod
    def _error_response(url, code, text):
        import httpx
        request = httpx.Request("POST", url)
        return httpx.Response(code, text=text, request=request)

    def test_http_error_returns_details(self):
        import asyncio
        import httpx
        from unittest.mock import Mock
        url = "https://api.example.com/v1/chat/completions"
        client = LLMClient(model="m")
        err_resp = self._error_response(url, 403, '{"model":"x"}')

        class Resp:
            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "boom",
                    request=httpx.Request("POST", url),
                    response=err_resp,
                )

        async def fake_post(url, json=None, headers=None, timeout=None):
            return Resp()

        async def run():
            client._client = Mock()
            client._client.post = fake_post
            return await client._try_chat(url, [{"role": "user", "content": "hi"}])

        result = asyncio.run(run())
        assert result["error"] == "http_403"
        assert result["error_details"]["status"] == 403
        assert "model" in result["error_details"]["body"]

    def test_failure_message_cloud_no_lmstudio_blame(self):
        client = LLMClient(url="https://opencode.ai/zen/v1/chat/completions")
        msg = client._failure_message(
            "http_403",
            {"status": 403, "body": '{"model":"nemotron-3-ultra-free"}'},
            "https://opencode.ai/zen/v1/chat/completions",
        )
        assert "opencode.ai" in msg
        assert "403" in msg or "запрещ" in msg
        assert "LM Studio" not in msg

    def test_failure_message_local_mentions_lmstudio(self):
        from src.llm_client import FALLBACK_URLS
        client = LLMClient(url="http://localhost:1234/v1/chat/completions")
        client.set_fallbacks(FALLBACK_URLS)
        msg = client._failure_message("connection_refused", {}, client.completions_url)
        assert "localhost:1234" in msg
        assert "LM Studio" in msg

    def test_failure_message_401_credits(self):
        client = LLMClient(url="https://opencode.ai/zen/v1/chat/completions")
        msg = client._failure_message(
            "http_401",
            {"status": 401, "body": "CreditsError: No payment method"},
            "https://opencode.ai/zen/v1/chat/completions",
        )
        assert "платёжн" in msg
        assert "CreditsError" in msg

    def test_failure_message_503_upstream(self):
        client = LLMClient(url="https://opencode.ai/zen/v1/chat/completions")
        msg = client._failure_message(
            "http_503",
            {"status": 503, "body": "Upstream request failed: Endpoint is unavailable."},
            "https://opencode.ai/zen/v1/chat/completions",
        )
        assert "upstream" in msg.lower()
        assert "opencode.ai" in msg
        assert "LM Studio" not in msg

    def test_5xx_and_429_are_retryable(self):
        client = LLMClient()
        assert client._is_retryable("http_503")
        assert client._is_retryable("http_502")
        assert client._is_retryable("http_500")
        assert client._is_retryable("http_429")
        assert not client._is_retryable("http_401")
        assert not client._is_retryable("http_403")
        assert not client._is_retryable("invalid_response")

    def test_transient_5xx_gets_extended_retry_budget(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        import src.llm_client as lc

        client = LLMClient(url="https://relay.example/v1/chat/completions", model="m")
        flaky = [{"error": "http_503"}] * 5 + [{"choices": [{"message": {"content": "ok"}}]}]
        client._try_chat = AsyncMock(side_effect=flaky)
        client._client = Mock()

        async def run():
            return await client.chat([{"role": "user", "content": "hi"}], max_tokens=8)

        with __import__("unittest").mock.patch.object(
            lc, "get_llm_retry_config",
            side_effect=lambda key, default: {"max_attempts": 2, "backoff_seconds": 0.01}.get(key, default),
        ):
            result = asyncio.run(run())

        assert "error" not in result, result
        assert client._try_chat.call_count == 6

    def test_non_transient_stops_after_base_budget(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        from unittest import mock
        import src.llm_client as lc

        client = LLMClient(url="https://api.example.com/v1/chat/completions", model="m")
        denied = [{"error": "http_403", "error_details": {"status": 403, "body": ""}}]
        client._try_chat = AsyncMock(side_effect=denied)
        client._client = Mock()

        async def run():
            return await client.chat([{"role": "user", "content": "hi"}], max_tokens=8)

        with mock.patch.object(
            lc, "get_llm_retry_config",
            side_effect=lambda key, default: {"max_attempts": 2, "backoff_seconds": 0.01}.get(key, default),
        ):
            result = asyncio.run(run())

        assert "403" in result["error"] or "запрещ" in result["error"]
        assert client._try_chat.call_count == 1
