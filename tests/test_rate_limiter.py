import asyncio
import time

import pytest

from src.rate_limiter import DomainRateLimiter


def test_init_defaults():
    rl = DomainRateLimiter()
    assert rl.min_interval == 1.5
    assert rl.max_rpm == 20


def test_cleanup_removes_old():
    rl = DomainRateLimiter()
    rl.request_history["x.ru"] = [time.time() - 120, time.time() - 1]
    rl._cleanup_old_requests("x.ru")
    assert len(rl.request_history["x.ru"]) == 1


def test_get_stats_empty():
    rl = DomainRateLimiter()
    stats = rl.get_stats("x.ru")
    assert stats["requests_last_minute"] == 0


def test_wait_if_needed_immediate():
    rl = DomainRateLimiter(min_interval=0)

    async def test():
        await rl.wait_if_needed("https://x.ru/page")
        assert len(rl.request_history["x.ru"]) == 1

    asyncio.run(test())


def test_min_interval_respected():
    rl = DomainRateLimiter(min_interval=0.2)

    async def test():
        await rl.wait_if_needed("https://x.ru/page")
        t0 = time.time()
        await rl.wait_if_needed("https://x.ru/other")
        elapsed = time.time() - t0
        assert elapsed >= 0.18

    asyncio.run(test())


def test_rpm_limiter_waits():
    rl = DomainRateLimiter(min_interval=0, max_requests_per_minute=2)
    # Уже совершили max_rpm запросов к домену
    now = time.time()
    rl.request_history["x.ru"] = [now, now]
    rl.last_request["x.ru"] = now

    async def test():
        # Старый первый запрос → wait_time = 60 - (now - now) = 60s.
        # Ускоряем: подменяем старый timestamp, чтобы ожидание было малым.
        rl.request_history["x.ru"][0] = time.time() - 59.0
        t0 = time.time()
        await rl.wait_if_needed("https://x.ru/page")
        assert time.time() - t0 >= 0.8

    asyncio.run(test())


def test_different_domains_independent():
    rl = DomainRateLimiter(min_interval=0)

    async def test():
        await rl.wait_if_needed("https://a.ru/page")
        await rl.wait_if_needed("https://b.ru/page")
        assert len(rl.request_history["a.ru"]) == 1
        assert len(rl.request_history["b.ru"]) == 1

    asyncio.run(test())


def test_normalize_domain_strips_www_and_scheme():
    from src.rate_limiter import _normalize_domain
    assert _normalize_domain("https://www.vseinstrumenti.ru/cat") == "vseinstrumenti.ru"
    assert _normalize_domain("http://vseinstrumenti.ru/") == "vseinstrumenti.ru"
    assert _normalize_domain("vseinstrumenti.ru") == "vseinstrumenti.ru"


def test_site_override_min_interval():
    rl = DomainRateLimiter(min_interval=0.0, jitter=0.0,
                           site_overrides={"vseinstrumenti.ru": {"min_interval": 0.2}})

    async def test():
        await rl.wait_if_needed("https://www.vseinstrumenti.ru/a")
        t0 = time.time()
        await rl.wait_if_needed("https://www.vseinstrumenti.ru/b")
        elapsed = time.time() - t0
        assert elapsed >= 0.18

    asyncio.run(test())


def test_record_block_sets_cooldown():
    rl = DomainRateLimiter(min_interval=0.0, jitter=0.0, cooldown_seconds=300)
    rl.record_block("https://www.vseinstrumenti.ru/x")
    stats = rl.get_stats("vseinstrumenti.ru")
    assert stats["blocked_until"] > time.time()
    assert len(rl.request_history["vseinstrumenti.ru"]) == 0


def test_wait_respects_cooldown():
    rl = DomainRateLimiter(min_interval=0.0, jitter=0.0, cooldown_seconds=0.3)
    rl.record_block("https://vseinstrumenti.ru/x", cooldown_seconds=0.3)

    async def test():
        t0 = time.time()
        await rl.wait_if_needed("https://vseinstrumenti.ru/x")
        assert time.time() - t0 >= 0.25

    asyncio.run(test())


def test_cooldown_uses_site_override():
    rl = DomainRateLimiter(min_interval=0.0, jitter=0.0, cooldown_seconds=1.0,
                           site_overrides={"vseinstrumenti.ru": {"cooldown_seconds": 0.3}})
    rl.record_block("https://vseinstrumenti.ru/x")
    stats = rl.get_stats("vseinstrumenti.ru")
    assert 0.25 < stats["blocked_until"] - time.time() < 0.35


def test_jitter_never_shorter_than_min_interval():
    rl = DomainRateLimiter(min_interval=0.2, jitter=0.0)

    async def test():
        await rl.wait_if_needed("https://x.ru/a")
        t0 = time.time()
        await rl.wait_if_needed("https://x.ru/b")
        assert time.time() - t0 >= 0.18

    asyncio.run(test())
