import asyncio

import pytest

from src.site_analyzer import SiteAnalyzer


class FakeBridge:
    def __init__(self, evaluate_results):
        self.evaluate_results = list(evaluate_results)
        self.snapshot = "<html></html>"
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append(name)
        if name == "browser_evaluate":
            if self.evaluate_results:
                return self.evaluate_results.pop(0)
            return "false"
        if name == "browser_navigate":
            return "ok"
        if name == "browser_snapshot":
            return self.snapshot
        return "ok"


def test_detect_spa_nextjs():
    async def test():
        bridge = FakeBridge(["true", "false", '{"total_elements": 100, "max_depth": 15}'])
        analyzer = SiteAnalyzer()
        is_spa = await analyzer._detect_spa(bridge)
        assert is_spa is True

    asyncio.run(test())


def test_detect_spa_ssr():
    async def test():
        bridge = FakeBridge(["false", "false", "false", "false", "false", "false", "false", "false", "false"])
        analyzer = SiteAnalyzer()
        is_spa = await analyzer._detect_spa(bridge)
        assert is_spa is False

    asyncio.run(test())


def test_detect_antibot():
    async def test():
        bridge = FakeBridge([])
        bridge.snapshot = "Cloudflare verification challenge"
        analyzer = SiteAnalyzer()
        assert await analyzer._detect_antibot(bridge) is True

    asyncio.run(test())


def test_detect_no_antibot():
    async def test():
        bridge = FakeBridge([])
        bridge.snapshot = "normal product page"
        analyzer = SiteAnalyzer()
        assert await analyzer._detect_antibot(bridge) is False

    asyncio.run(test())


def test_dom_stats():
    async def test():
        bridge = FakeBridge(['{"total_elements": 500, "max_depth": 20}'])
        analyzer = SiteAnalyzer()
        stats = await analyzer._get_dom_stats(bridge)
        assert stats["total_elements"] == 500
        assert stats["max_depth"] == 20

    asyncio.run(test())


def test_dom_stats_error_fallback():
    async def test():
        bridge = FakeBridge(["error: boom"])
        analyzer = SiteAnalyzer()
        stats = await analyzer._get_dom_stats(bridge)
        assert stats["total_elements"] == 0

    asyncio.run(test())


def test_recommend_strategy():
    analyzer = SiteAnalyzer()
    assert analyzer._recommend_strategy(False, False) == "STANDARD"
    assert analyzer._recommend_strategy(True, False) == "SPA_AWARE"
    assert analyzer._recommend_strategy(False, True) == "CAUTIOUS"


def test_analyze_site_caches_profile():
    async def test():
        bridge = FakeBridge(["true", '{"total_elements": 10, "max_depth": 5}'])
        bridge.snapshot = "normal"
        analyzer = SiteAnalyzer()
        profile = await analyzer.analyze_site("https://example.com", bridge)
        assert profile["domain"] == "example.com"
        assert profile["is_spa"] is True
        assert analyzer.get_profile("https://example.com/page") is not None

    asyncio.run(test())


def test_domain_of():
    assert SiteAnalyzer._domain_of("https://www.Tinko.ru/catalog") == "tinko.ru"
    assert SiteAnalyzer._domain_of("https://x.ru") == "x.ru"
