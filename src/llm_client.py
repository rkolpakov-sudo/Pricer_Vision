import asyncio
import httpx
import json
import logging
from typing import Any

from src.config_loader import get_llm_retry_config

logger = logging.getLogger("pricer.llm")

FALLBACK_URLS = [
    ("LM Studio", "http://localhost:1234/v1/chat/completions"),
    ("Ollama", "http://localhost:11434/v1/chat/completions"),
    ("llama.cpp", "http://localhost:8080/v1/chat/completions"),
]


class LLMClient:
    def __init__(self, url: str = "http://localhost:1234/v1/chat/completions",
                 timeout: float = 150.0, model: str = "",
                 temperature: float = 0.3):
        self.completions_url = url
        self.base_url = "/".join(url.split("/")[:-2]) if "/chat/completions" in url else url
        self.timeout = timeout
        self.model = model
        self.temperature = temperature
        self._client: httpx.AsyncClient | None = None
        self._detected = False
        self._fallback_urls: list[tuple[str, str]] = []
        self._active_url: str | None = None
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def reset_usage(self):
        """Обнуляет накопители токенов (вход/выход LLM)."""
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def set_fallbacks(self, fallbacks: list[tuple[str, str]]):
        self._fallback_urls = fallbacks

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=3),
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def detect_model(self) -> str | None:
        if not self._client:
            return None
        for name, url in [("primary", self.base_url)] + self._fallback_urls:
            u = "/".join(url.split("/")[:-2]) if "/chat/completions" in url else url
            try:
                r = await self._client.get(f"{u}/models", timeout=5.0)
                if r.status_code == 200:
                    data = r.json()
                    models = data.get("data", [])
                    if models:
                        model_id = models[0].get("id", "")
                        self.model = model_id
                        self._detected = True
                        logger.info(f"{name}: {model_id}")
                        return model_id
            except (httpx.TimeoutException, httpx.ConnectError, json.JSONDecodeError):
                logger.warning(f"{name} not detected at {u}")
        return None

    def _is_retryable(self, error: str) -> bool:
        return error in ("http_400", "connection_refused", "timeout")

    async def chat(self, messages: list[dict],
                   tools: list[dict] | None = None,
                   force_json: bool = False,
                   *,
                   temperature: float | None = None,
                   max_tokens: int | None = None) -> dict[str, Any]:
        if not self._client:
            return {"error": "client not initialized"}

        temp = temperature if temperature is not None else self.temperature
        tok = max_tokens if max_tokens is not None else 8192

        # if we already found a working URL, use it directly
        if self._active_url:
            result = await self._try_chat(self._active_url, messages, tools, force_json, temp, tok)
            if "error" not in result:
                return result
            if self._is_retryable(result["error"]):
                logger.warning(f"Active LLM failed ({result['error']}), re-probing...")
                self._active_url = None
            else:
                return result

        urls = [(None, self.completions_url)]
        urls.extend(self._fallback_urls)

        last_error = ""
        max_attempts = get_llm_retry_config("max_attempts", 2)
        backoff_seconds = get_llm_retry_config("backoff_seconds", 1.0)
        for attempt in range(max_attempts):
            for name, url in urls:
                if name:
                    logger.info(f"Trying {name}...")
                result = await self._try_chat(url, messages, tools, force_json, temp, tok)
                if "error" not in result:
                    self._active_url = url
                    return result
                last_error = result["error"]
                if not self._is_retryable(last_error):
                    break
                logger.warning(f"{name or 'primary'} {last_error}, trying fallback...")
            if attempt < max_attempts - 1:
                delay = backoff_seconds * (2 ** attempt)
                logger.warning(f"LLM retry {attempt + 1}/{max_attempts} in {delay:.1f}s...")
                await asyncio.sleep(delay)
        return {"error": f"LLM недоступен: {last_error}. Проверьте LM Studio / Ollama"}

    async def _try_chat(self, url: str, messages: list[dict],
                        tools: list[dict] | None = None,
                        force_json: bool = False,
                        temperature: float | None = None,
                        max_tokens: int | None = None) -> dict[str, Any]:
        model_name = self.model
        if not model_name and not self._detected:
            await self.detect_model()
            model_name = self.model
        if not model_name:
            model_name = self.model or "qwen2.5"

        body = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else 8192,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "none" if force_json else "auto"

        try:
            response = await self._client.post(
                url, json=body, timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage") or {}
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            return data
        except httpx.TimeoutException:
            logger.error(f"LLM timeout ({self.timeout}s)")
            return {"error": "timeout"}
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM HTTP {e.response.status_code} from {url}")
            return {"error": f"http_{e.response.status_code}"}
        except httpx.ConnectError:
            logger.error(f"LLM connection refused at {url}")
            return {"error": "connection_refused"}
        except json.JSONDecodeError:
            logger.error("LLM bad JSON response")
            return {"error": "invalid_response"}
