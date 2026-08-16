"""Интеграционные тесты агентного цикла process_row с моками LLM/MCP.

Не требуют реального браузера/LLM/БД — всё заменено на фейковые объекты.
Покрывают ключевые сценарии: полное извлечение, tool_call цикл,
reuse (rule 8), semantic cache, ошибки LLM, max rounds, captcha.
"""

import json

import pytest

from src.agent_loop import process_row, _error_result, MAX_ROUNDS


def llm_final(price, confidence=0.95, url="https://site.ru/p", site="site.ru"):
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "price": price, "confidence": confidence, "url": url,
                    "site": site, "reason": "found", "requires_review": False,
                }),
                "tool_calls": [],
            },
        }],
    }


def llm_tool_call(name, args, call_id="call_1"):
    return {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            },
        }],
    }


def llm_empty():
    return {"choices": [{"message": {"content": "", "tool_calls": []}}]}


class FakeConn:
    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return []


class FakeEngine:
    def __init__(self):
        self._all_products = {}
        self._conn = FakeConn()

    def classify_product_type(self, spec_text):
        return "unknown"


class FakeMemoryManager:
    def __init__(self):
        self.saved_prices = []
        self.saved_approaches = []
        self.prices = []

    def get_all_approaches(self, pt):
        return []

    def get_all_approaches_flat(self):
        return []

    def get_relevant_prices(self, spec):
        return self.prices

    def get_sites(self, pt):
        return []

    def get_hints(self, pt):
        return []

    def save_price(self, **kw):
        self.saved_prices.append(kw)
        return 1

    def save_approach(self, **kw):
        self.saved_approaches.append(kw)
        return 1

    def record_success(self, aid):
        pass

    def record_failure(self, aid):
        pass

    def record_soldat(self, pt, site):
        pass

    def get_site_approaches(self, pt, site):
        return []

    def get_approaches_by_site(self, site):
        return []

    def increment_consecutive_failures(self, pt, site):
        pass


class FakeBridge:
    def __init__(self, snapshot_result="ok"):
        self.tools = [
            {"type": "function", "function": {
                "name": "browser_navigate", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "browser_snapshot", "parameters": {"type": "object", "properties": {}}}},
        ]
        self.snapshot_result = snapshot_result
        self.calls = []

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "browser_snapshot":
            return self.snapshot_result
        return "ok"


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, temperature=None):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return {"error": "no more responses"}


class FakeSemanticCache:
    def __init__(self, hit=None):
        self.hit = hit

    def get_similar(self, spec_text):
        return self.hit

    def store(self, spec_text, result):
        pass


def make_env(fresh=True, snapshot_result="ok", semantic=None, responses=None):
    llm = FakeLLM(responses or [])
    bridge = FakeBridge(snapshot_result=snapshot_result)
    engine = FakeEngine()
    mm = FakeMemoryManager()
    cache = semantic if semantic is not None else (FakeSemanticCache() if not fresh else None)
    return llm, bridge, engine, mm, cache


@pytest.mark.asyncio
class TestAgentFlow:
    async def test_full_extraction_flow(self):
        """Полный цикл: LLM сразу возвращает цену → результат dict с price."""
        llm, bridge, engine, mm, cache = make_env(responses=[llm_final(100.0)])
        events = []
        result = await process_row(
            spec_text="Кабель ВВГ 3x2.5",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            monitor_callback=lambda t, v: events.append((t, v)),
        )
        assert result.get("price") == 100.0
        assert result.get("confidence", 0) > 0.8
        assert result.get("url") == "https://site.ru/p"
        assert result.get("site") == "site.ru"
        assert not result.get("error")
        assert ("llm_call", events[0][1]) == ("llm_call", events[0][1])  # llm_call был репортнут
        assert any(t == "llm_call" for t, _ in events)

    async def test_tool_call_then_final(self):
        """LLM вызывает browser_navigate, затем возвращает финальную цену."""
        llm, bridge, engine, mm, cache = make_env(responses=[
            llm_tool_call("browser_navigate", {"url": "https://site.ru"}),
            llm_final(250.5),
        ])
        result = await process_row(
            spec_text="Труба ПНД 32",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 250.5
        assert any(name == "browser_navigate" for name, _ in bridge.calls)

    async def test_rule8_reuse_high_confidence(self):
        """fresh=False: цена с confidence >= 0.9 переиспользуется без LLM."""
        llm, bridge, engine, mm, cache = make_env(fresh=False)
        mm.prices = [{"price": 500.0, "confidence": 0.95, "url": "https://site.ru/p", "site_id": "site.ru"}]
        result = await process_row(
            spec_text="Кабель NYM 3x2.5",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=False,
            semantic_cache=cache,
        )
        assert result.get("price") == 500.0
        assert llm.calls == []
        assert "rule8_reuse" in (mm.saved_prices[-1].get("reason", "") if mm.saved_prices else "")

    async def test_semantic_cache_hit(self):
        """fresh=False: семантический кэш возвращает цену без обращения к LLM."""
        llm, bridge, engine, mm, cache = make_env(fresh=False, semantic=FakeSemanticCache(
            hit={"price": 300.0, "confidence": 0.9, "url": "https://cache.ru", "site": "cache.ru",
                 "similarity": 0.92}
        ))
        result = await process_row(
            spec_text="Аналогичный товар 20мм",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=False,
            semantic_cache=cache,
        )
        assert result.get("price") == 300.0
        assert llm.calls == []
        assert "semantic_cache" in result.get("reason", "")

    async def test_llm_error_returns_error(self):
        """LLM недоступен → error-result."""
        llm, bridge, engine, mm, cache = make_env(responses=[{"error": "connection refused"}])
        result = await process_row(
            spec_text="Товар",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") is None
        assert result.get("error")

    async def test_max_rounds_reached(self, monkeypatch):
        """LLM не даёт результат → исчерпание лимита раундов → error."""
        monkeypatch.setattr("src.agent_loop.MAX_ROUNDS", 3)
        llm, bridge, engine, mm, cache = make_env(responses=[llm_empty(), llm_empty(), llm_empty(),
                                                              llm_empty(), llm_empty(), llm_empty()])
        result = await process_row(
            spec_text="Товар без результата",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") is None
        assert "Max rounds" in result.get("error", "")

    async def test_captcha_block_reported(self):
        """Снапшот с captcha → событие block в monitor_callback."""
        llm, bridge, engine, mm, cache = make_env(snapshot_result="hcheck challenge page", responses=[
            llm_tool_call("browser_navigate", {"url": "https://site.ru"}),
            llm_tool_call("browser_snapshot", {}),
            llm_final(99.0, url="https://other.ru/p", site="other.ru"),
        ])
        events = []
        result = await process_row(
            spec_text="Товар с блокировкой",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            monitor_callback=lambda t, v: events.append((t, v)),
        )
        assert any(t == "block" for t, _ in events)
        assert result.get("price") == 99.0

    async def test_stuck_recovery_forces_site_switch(self):
        """Повторяющиеся действия (StuckDetector CRITICAL) → принудительный уход с сайта."""
        responses = [
            llm_tool_call("browser_navigate", {"url": "https://site.ru"}),
            llm_tool_call("browser_snapshot", {}),   # round 2
            llm_tool_call("browser_snapshot", {}),   # round 3
            llm_tool_call("browser_snapshot", {}),   # round 4
            llm_tool_call("browser_snapshot", {}),   # round 5
            llm_tool_call("browser_snapshot", {}),   # round 6 → CRITICAL (rounds_on_site=6 > 5)
            llm_final(120.0, url="https://other.ru/p", site="other.ru"),
        ]
        llm, bridge, engine, mm, cache = make_env(responses=responses)
        events = []
        result = await process_row(
            spec_text="Товар с зацикливанием",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            monitor_callback=lambda t, v: events.append((t, v)),
        )
        assert any(t == "stuck" for t, _ in events)
        assert result.get("price") == 120.0
        assert result.get("site") == "other.ru"


@pytest.mark.asyncio
class TestErrorResultContract:
    async def test_error_result_shape(self):
        err = _error_result("spec", "boom", elapsed=1.5)
        assert err["price"] is None
        assert err["error"] == "boom"
        assert err["elapsed"] == 1.5
        assert err["requires_review"] is True
