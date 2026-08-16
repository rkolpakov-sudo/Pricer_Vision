import asyncio
import json

import pytest

from src.human_behavior import HumanBehavior


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.bbox = None

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "browser_evaluate" and "getBoundingClientRect" in str(args):
            return json.dumps({"x": 10, "y": 10, "w": 100, "h": 50})
        return "ok"


def test_get_random_viewport():
    vp = HumanBehavior.get_random_viewport()
    assert "width" in vp and "height" in vp
    assert vp["width"] > 0


def test_random_pause():
    async def test():
        import time
        t0 = time.time()
        await HumanBehavior.random_pause(0.05, 0.05)
        assert time.time() - t0 >= 0.04

    asyncio.run(test())


def test_human_click_with_bbox():
    async def test():
        bridge = FakeBridge()
        await HumanBehavior.human_click("#btn", bridge)
        names = [c[0] for c in bridge.calls]
        assert "browser_evaluate" in names
        assert "browser_click" in names
        click = [c for c in bridge.calls if c[0] == "browser_click"][0]
        assert click[1]["target"] == "#btn"

    asyncio.run(test())


def test_human_click_fallback_on_error():
    async def test():
        class BadBridge:
            async def call_tool(self, name, args):
                if name == "browser_evaluate":
                    return "error: boom"
                return "ok"
        bridge = BadBridge()
        result = await HumanBehavior.human_click("#btn", bridge)
        assert result == "ok"

    asyncio.run(test())


def test_human_type_single_char_calls():
    async def test():
        class CountingBridge:
            def __init__(self):
                self.calls = []
            async def call_tool(self, name, args):
                self.calls.append(name)
                return "ok"
        bridge = CountingBridge()
        await HumanBehavior.human_type("#input", "ab", bridge)
        assert bridge.calls.count("browser_click") == 1
        assert bridge.calls.count("browser_type") == 2

    asyncio.run(test())


def test_human_scroll():
    async def test():
        bridge = FakeBridge()
        await HumanBehavior.human_scroll("down", 100, bridge)
        names = [c[0] for c in bridge.calls]
        assert "browser_evaluate" in names
        assert all("scrollBy" in str(c[1]) for c in bridge.calls if c[0] == "browser_evaluate")

    asyncio.run(test())
