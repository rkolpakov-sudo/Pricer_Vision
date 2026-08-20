import asyncio
import random
import time
from collections import defaultdict
from urllib.parse import urlparse

DEFAULT_MIN_INTERVAL = 1.5
DEFAULT_MAX_REQUESTS_PER_MINUTE = 20
DEFAULT_JITTER = 1.0
DEFAULT_COOLDOWN_SECONDS = 300
WINDOW_SECONDS = 60


def _normalize_domain(url: str) -> str:
    """Домен без www и схемы: 'https://www.vseinstrumenti.ru/a' → 'vseinstrumenti.ru'."""
    host = urlparse(url).netloc or url
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class DomainRateLimiter:
    """Ограничение частоты запросов к каждому домену.

    Применяется к ЛЮБОМУ browser-инструменту (не только navigate): перед
    каждым действием агента вызывается wait_if_needed(url), чтобы соблюдать
    минимальный интервал, RPM-лимит и cooldown после бана.

    Ключевые особенности:
    - Джиттер: фактическая пауза = min_interval + random(0, jitter), чтобы
      не создавать идеально равномерный машинный ритм (выдаёт бота).
    - Per-site overrides: для чувствительных сайтов (vseinstrumenti.ru)
      можно задать свои min_interval/max_rpm/cooldown через settings.yaml.
    - Cooldown: после бана/captcha сайт не трогаем cooldown_seconds
      (record_block), а не бросаем его мгновенно.
    """

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL,
                 max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE,
                 jitter: float = DEFAULT_JITTER,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 site_overrides: dict | None = None):
        self.min_interval = min_interval
        self.max_rpm = max_requests_per_minute
        self.jitter = jitter
        self.cooldown_seconds = cooldown_seconds
        self.site_overrides = site_overrides or {}
        self.request_history: dict[str, list[float]] = defaultdict(list)
        self.last_request: dict[str, float] = defaultdict(float)
        self._blocked_until: dict[str, float] = defaultdict(float)

    def _settings_for(self, domain: str) -> tuple[float, int, float]:
        ov = self.site_overrides.get(domain) or {}
        min_interval = float(ov.get("min_interval", self.min_interval))
        max_rpm = int(ov.get("max_requests_per_minute", self.max_rpm))
        cooldown = float(ov.get("cooldown_seconds", self.cooldown_seconds))
        return min_interval, max_rpm, cooldown

    async def wait_if_needed(self, url: str):
        domain = _normalize_domain(url)
        now = time.time()

        # Cooldown после бана: ждём до конца паузы, прежде чем трогать домен
        blocked_until = self._blocked_until.get(domain, 0.0)
        if blocked_until > now:
            await asyncio.sleep(blocked_until - now)
            self._blocked_until.pop(domain, None)

        now = time.time()
        min_interval, max_rpm, _ = self._settings_for(domain)

        elapsed = now - self.last_request[domain]
        target = min_interval + random.uniform(0.0, self.jitter) if self.jitter > 0 else min_interval
        if elapsed < target:
            await asyncio.sleep(target - elapsed)

        self._cleanup_old_requests(domain)
        if len(self.request_history[domain]) >= max_rpm:
            oldest = self.request_history[domain][0]
            wait_time = WINDOW_SECONDS - (time.time() - oldest)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        now = time.time()
        self.request_history[domain].append(now)
        self.last_request[domain] = now

    def record_block(self, url: str, cooldown_seconds: float | None = None):
        """Зафиксировать бан/captcha: домен не трогаем cooldown_seconds.

        Отличие от старого поведения (мгновенный SWITCH_SITE): после бана
        сайт становится доступен снова через паузу, а не выбрасывается
        из работы навсегда. Текущая история запросов сбрасывается.
        """
        domain = _normalize_domain(url)
        _, _, cooldown = self._settings_for(domain)
        if cooldown_seconds is not None:
            cooldown = cooldown_seconds
        self._blocked_until[domain] = time.time() + cooldown
        self.request_history[domain] = []
        self.last_request[domain] = time.time()

    def _cleanup_old_requests(self, domain: str):
        cutoff = time.time() - WINDOW_SECONDS
        self.request_history[domain] = [
            ts for ts in self.request_history[domain] if ts > cutoff
        ]

    def get_stats(self, domain: str) -> dict:
        self._cleanup_old_requests(domain)
        last = self.last_request.get(domain, 0)
        return {
            "requests_last_minute": len(self.request_history[domain]),
            "seconds_since_last": max(0.0, time.time() - last) if last else 0.0,
            "blocked_until": self._blocked_until.get(domain, 0.0),
        }