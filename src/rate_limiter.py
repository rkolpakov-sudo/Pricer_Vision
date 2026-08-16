import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse

DEFAULT_MIN_INTERVAL = 1.5
DEFAULT_MAX_REQUESTS_PER_MINUTE = 20
WINDOW_SECONDS = 60


class DomainRateLimiter:
    """Ограничение частоты запросов к каждому домену.

    Перед browser_navigate на домен агент вызывает wait_if_needed(url),
    чтобы соблюдать минимальный интервал и RPM-лимит на домен.
    """

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL,
                 max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE):
        self.min_interval = min_interval
        self.max_rpm = max_requests_per_minute
        self.request_history: dict[str, list[float]] = defaultdict(list)
        self.last_request: dict[str, float] = defaultdict(float)

    async def wait_if_needed(self, url: str):
        domain = urlparse(url).netloc or url
        now = time.time()

        elapsed = now - self.last_request[domain]
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)

        self._cleanup_old_requests(domain)
        if len(self.request_history[domain]) >= self.max_rpm:
            oldest = self.request_history[domain][0]
            wait_time = WINDOW_SECONDS - (time.time() - oldest)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        now = time.time()
        self.request_history[domain].append(now)
        self.last_request[domain] = now

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
        }
