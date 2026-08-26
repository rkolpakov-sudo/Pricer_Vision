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
                 temperature: float = 0.3,
                 api_key: str = "",
                 headers: dict | None = None):
        self.completions_url = url
        self.base_url = "/".join(url.split("/")[:-2]) if "/chat/completions" in url else url
        self.timeout = timeout
        self.model = model
        self.temperature = temperature
        self.api_key = api_key
        self.headers = dict(headers or {})
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

    def _request_headers(self) -> dict:
        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

    async def detect_model(self) -> str | None:
        if not self._client:
            return None
        headers = self._request_headers()
        for name, url in [("primary", self.base_url)] + self._fallback_urls:
            u = "/".join(url.split("/")[:-2]) if "/chat/completions" in url else url
            try:
                r = await self._client.get(f"{u}/models", timeout=5.0, headers=headers or None)
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
        # 5xx/429 — временные сбои upstream (наблюдалось на opencode zen: 503
        # "Upstream request failed", через минуты тот же запрос 200) — повторяем.
        return (
            error in ("http_400", "http_429", "connection_refused", "timeout")
            or error.startswith("http_5")
        )

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
        last_details: dict = {}
        last_url = self.completions_url
        max_attempts = get_llm_retry_config("max_attempts", 2)
        backoff_seconds = get_llm_retry_config("backoff_seconds", 1.0)
        # Шлюзы-агрегаторы (opencode zen) балансируют между живыми и мёртвыми
        # upstream-репликами: тот же запрос даёт то 503, то 200. Для транзиентных
        # 5xx/429 — увеличенный бюджет бросков (паритет с автоповтором OpenAI SDK,
        # через который ходят hermes/opencode).
        max_total = max_attempts * 3
        fatal = False
        for attempt in range(max_total):
            for name, url in urls:
                if name:
                    logger.info(f"Trying {name}...")
                result = await self._try_chat(url, messages, tools, force_json, temp, tok)
                if "error" not in result:
                    self._active_url = url
                    return result
                last_error = result["error"]
                last_details = result.get("error_details") or {}
                last_url = url
                if not self._is_retryable(last_error):
                    # Не-retryable (401/403/4xx) — прекращаем ВСЕ попытки сразу.
                    fatal = True
                    break
                logger.warning(f"{name or 'primary'} {last_error}, trying fallback...")
            if fatal:
                break
            base_budget_spent = attempt >= max_attempts - 1
            transient_server = self._is_transient_server(last_error)
            if base_budget_spent and not transient_server:
                break
            if attempt < max_total - 1:
                delay = min(backoff_seconds * (2 ** (attempt % 4)), 3.0)
                logger.warning(
                    f"LLM retry {attempt + 1}/{max_total} in {delay:.1f}s "
                    f"({last_error})..."
                )
                await asyncio.sleep(delay)
        return {"error": self._failure_message(last_error, last_details, last_url)}

    @staticmethod
    def _is_transient_server(error: str) -> bool:
        return error == "http_429" or error.startswith("http_5")

    @staticmethod
    def _host(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc or url
        except Exception:
            return url

    def _failure_message(self, error: str, details: dict, url: str) -> str:
        """Провайдер-зависимое сообщение: не обвиняем LM Studio, когда запрос
        шёл на облако; для локальных серверов — прежняя подсказка."""
        host = self._host(url)
        status = details.get("status")
        snippet = (details.get("body") or "").strip().replace("\n", " ")[:200]
        local_chain = bool(self._fallback_urls)
        base = f"LLM недоступен [{host}]: {error}"
        if status == 401:
            hint = "ключ отклонён (нет платёжного метода или недействителен) — проверьте аккаунт провайдера"
            if "payment" in snippet.lower() or "credits" in snippet.lower():
                hint += f"; ответ: {snippet}"
        elif status == 403:
            hint = "доступ запрещён сервером (WAF/тариф/лимиты) — модель может быть недоступна этому ключу; проверьте кабинет провайдера"
            if snippet:
                hint += f"; ответ: {snippet}"
        elif status == 404:
            hint = "эндпоинт/модель не найдена — проверьте Base URL и имя модели"
        elif error == "http_503":
            hint = "upstream провайдера временно недоступен — повторите попытку позже"
            if snippet:
                hint += f"; ответ: {snippet}"
        elif status is not None and 500 <= status < 600:
            hint = "временная ошибка на стороне сервера — повторите попытку"
            if snippet:
                hint += f"; ответ: {snippet}"
        elif status is not None and status >= 400:
            hint = "сервер вернул ошибку"
            if snippet:
                hint += f"; ответ: {snippet}"
        elif error == "timeout":
            hint = "таймаут ожидания ответа"
        else:
            hint = "сервер недостижим (нет соединения)"
        if local_chain:
            hint += ". Проверьте LM Studio / Ollama"
        return f"{base}; {hint}"

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
                headers=self._request_headers() or None,
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
            code = e.response.status_code
            try:
                snippet = e.response.text[:300]
            except Exception:
                snippet = ""
            logger.error(f"LLM HTTP {code} from {url}; body: {snippet[:200]}")
            return {"error": f"http_{code}",
                    "error_details": {"status": code, "body": snippet, "url": url}}
        except httpx.ConnectError:
            logger.error(f"LLM connection refused at {url}")
            return {"error": "connection_refused"}
        except json.JSONDecodeError:
            logger.error("LLM bad JSON response")
            return {"error": "invalid_response"}
