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

    def get_relevant_prices(self, spec, strict_sizes=True, ignore_sizes=False):
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

    def has_matching_equivalence(self, spec_text, found_name):
        return False

    def record_matching_equivalence(self, spec_text, found_name):
        pass


class FakeBridge:
    def __init__(self, snapshot_result="ok", evaluate_result="ok"):
        self.tools = [
            {"type": "function", "function": {
                "name": "browser_navigate", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "browser_snapshot", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "browser_evaluate", "parameters": {"type": "object", "properties": {}}}},
        ]
        self.snapshot_result = snapshot_result
        self.evaluate_result = evaluate_result
        self.calls = []

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "browser_snapshot":
            return self.snapshot_result
        if name == "browser_evaluate":
            return self.evaluate_result
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


def make_env(fresh=True, snapshot_result="ok", semantic=None, responses=None, evaluate_result="ok"):
    llm = FakeLLM(responses or [])
    bridge = FakeBridge(snapshot_result=snapshot_result, evaluate_result=evaluate_result)
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
        assert result.get("confidence", 0) == 0.7
        assert result.get("requires_review") is True
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

    async def test_stuck_diagnostic_recovery_on_card(self):
        """Зацикливание на ОТКРЫТОЙ карточке товара → диагностическое сообщение
        (а не слепой приказ уйти с сайта), и LLM может извлечь цену."""
        card_url = "https://site.ru/catalog/293/306/i46584/v155997/"
        script = "() => document.querySelector('.price')"
        responses = [
            llm_tool_call("browser_navigate", {"url": card_url}),
            llm_tool_call("browser_evaluate", {"function": script}),  # round 2
            llm_tool_call("browser_evaluate", {"function": script}),  # round 3
            llm_tool_call("browser_evaluate", {"function": script}),  # round 4
            llm_tool_call("browser_evaluate", {"function": script}),  # round 5
            llm_tool_call("browser_evaluate", {"function": script}),  # round 6 → CRITICAL (rounds_on_site=6 > 5)
            llm_final(7201.30, url=card_url, site="site.ru"),
        ]
        llm, bridge, engine, mm, cache = make_env(responses=responses)
        events = []
        result = await process_row(
            spec_text="Компенсатор сильфонный Ду15",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            monitor_callback=lambda t, v: events.append((t, v)),
        )
        assert any(t == "stuck" for t, _ in events)
        diagnostic_call = llm.calls[-1]
        last_content = diagnostic_call[-1].get("content", "")
        assert "Карточка товара ОТКРЫТА" in last_content
        assert "ПРОАНАЛИЗИРУЙ" in last_content
        assert "Принудительно переключись" not in last_content
        assert result.get("price") == 7201.30
        assert result.get("site") == "site.ru"

    async def test_stuck_diagnostic_cap_forces_switch(self):
        """После капа диагностических подсказок (2) при повторном зацикливании —
        принудительный уход с сайта."""
        card_url = "https://site.ru/product/42"
        script = "() => document.querySelector('.price')"
        responses = []
        # раунды: navigate + 5 одинаковых evaluate → CRITICAL, диагностика №1
        responses += [llm_tool_call("browser_navigate", {"url": card_url})]
        responses += [llm_tool_call("browser_evaluate", {"function": script})] * 5
        # после диагностики №1 агент снова 6 одинаковых evaluate → CRITICAL, диагностика №2
        responses += [llm_tool_call("browser_evaluate", {"function": script})] * 6
        # после диагностики №2 снова 6 одинаковых → CRITICAL, cap исчерпан → принудительный уход
        responses += [llm_tool_call("browser_evaluate", {"function": script})] * 6
        responses += [llm_final(50.0, url="https://other.ru/p", site="other.ru")]
        llm, bridge, engine, mm, cache = make_env(responses=responses)
        result = await process_row(
            spec_text="Товар с хроническим зацикливанием",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 50.0
        assert result.get("site") == "other.ru"
        # принудительный уход был отражён в сообщении
        assert any("Принудительно переключись" in m.get("content", "")
                   for call in llm.calls for m in call if isinstance(m.get("content"), str))

    async def test_negative_cache_skips_search(self):
        """Товар из отрицательного кэша пропускается без вызова LLM/браузера."""
        from src.session_cache import NegativeCache
        llm, bridge, engine, mm, cache = make_env(responses=[llm_final(100.0)])
        neg = NegativeCache()
        neg.record("Труба ПНД 32")
        neg.record("Труба ПНД 32")
        result = await process_row(
            spec_text="Труба ПНД 32",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            negative_cache=neg,
        )
        assert result.get("price") is None
        assert result.get("error") == "not_found_cached"
        assert result.get("requires_review") is True
        assert llm.calls == []
        assert bridge.calls == []

    async def test_negative_cache_one_failure_still_searches(self):
        """Одна неудача — товар ещё не в блокировке, поиск выполняется."""
        from src.session_cache import NegativeCache
        llm, bridge, engine, mm, cache = make_env(responses=[llm_final(100.0)])
        neg = NegativeCache()
        neg.record("Труба ПНД 32")
        result = await process_row(
            spec_text="Труба ПНД 32",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
            negative_cache=neg,
        )
        assert result.get("price") == 100.0
        assert llm.calls  # поиск состоялся

    async def test_empty_probe_guidance_injected(self):
        """3 пустых поисковых зонда подряд на одном сайте → руководство
        переключиться на другой сайт (ранний выход из бесплодного поиска)."""
        empty_probe_script = "() => document.querySelectorAll('.price').length"
        responses = [
            llm_tool_call("browser_navigate", {"url": "https://site.ru"}),
            llm_tool_call("browser_evaluate", {"function": empty_probe_script}),  # 1-й пустой
            llm_tool_call("browser_evaluate", {"function": empty_probe_script}),  # 2-й пустой
            llm_tool_call("browser_evaluate", {"function": empty_probe_script}),  # 3-й пустой → guidance
            llm_final(500.0, url="https://other.ru/p", site="other.ru"),
        ]
        llm, bridge, engine, mm, cache = make_env(
            responses=responses, evaluate_result="[]")
        result = await process_row(
            spec_text="Товар, которого нет на сайте",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 500.0
        guidance_seen = any(
            "раз подряд вернул пустой" in m.get("content", "")
            for call in llm.calls for m in call
            if isinstance(m.get("content"), str)
        )
        assert guidance_seen

    async def test_empty_probe_not_fired_on_real_price(self):
        """Пустой зонд с ценой не считается пустым — guidance не инжектится."""
        responses = [
            llm_tool_call("browser_navigate", {"url": "https://site.ru"}),
            llm_tool_call("browser_evaluate", {"function": "() => 1"}),  # результат с ценой
            llm_tool_call("browser_evaluate", {"function": "() => 1"}),  # с ценой
            llm_tool_call("browser_evaluate", {"function": "() => 1"}),  # с ценой
            llm_final(700.0, url="https://site.ru/p", site="site.ru"),
        ]
        llm, bridge, engine, mm, cache = make_env(
            responses=responses, evaluate_result="7 201,30 Р")
        result = await process_row(
            spec_text="Товар с ценой",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 700.0
        assert not any(
            "раз подряд вернул пустой" in m.get("content", "")
            for call in llm.calls for m in call
            if isinstance(m.get("content"), str)
        )

    async def test_family_page_save_rejected(self):
        """Сохранение цены с семейной страницы (/i<id>/ без /vN/) отклоняется
        и не попадает в БД."""
        responses = [
            llm_tool_call("save_confirmed_price", {
                "product_name": "Клапан балансировочный автомат латунь APT-R Ду15",
                "price": 15676.8, "confidence": 0.95,
                "url": "https://www.santech.ru/catalog/337/340/i1322/",
                "site": "santech.ru",
            }),
            llm_final(15676.8, url="https://www.santech.ru/catalog/337/340/i1322/v6/", site="santech.ru"),
        ]
        llm, bridge, engine, mm, cache = make_env(responses=responses)
        result = await process_row(
            spec_text="Клапан балансировочный авт. Ду15",
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert not any("i1322/" in (p.get("url", "") or "") for p in mm.saved_prices)
        assert result.get("price") == 15676.8
        assert result.get("url", "").endswith("v6/")


@pytest.mark.asyncio
class TestEquivalenceLearning:
    async def test_confirm_mismatch_records_equivalence(self, graph_engine):
        """Подтверждённое LLM несоответствие запоминается как эквивалентная пара."""
        from src.memory_manager import MemoryManager

        mm = MemoryManager(graph_engine)
        spec = "Компенсатор сильфонный под приварку Ду40"
        found = "Компенсатор сильфонный осевой многослойный б/кожух"
        llm = FakeLLM([llm_tool_call("save_confirmed_price", {
            "product_name": found,
            "price": 5088.5, "confidence": 0.8,
            "url": "https://site.ru/i1/v2/", "site": "site.ru",
            "confirm": True,
        })])
        bridge = FakeBridge()
        cache = FakeSemanticCache()
        result = await process_row(
            spec_text=spec,
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=graph_engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 5088.5
        assert result.get("requires_review") is True
        assert mm.has_matching_equivalence(spec, found) is True
        assert mm.get_matching_equivalences()[0]["spec_text"] == spec.lower()

    async def test_no_equivalence_without_confirm(self, graph_engine):
        """Без confirm несоответствие не запоминается и возвращается предупреждение."""
        from src.memory_manager import MemoryManager

        mm = MemoryManager(graph_engine)
        spec = "Компенсатор сильфонный под приварку Ду40"
        found = "Компенсатор сильфонный осевой многослойный б/кожух"
        llm = FakeLLM([llm_tool_call("save_confirmed_price", {
            "product_name": found,
            "price": 5088.5, "confidence": 0.8,
            "url": "https://site.ru/i1/v2/", "site": "site.ru",
        })])
        bridge = FakeBridge()
        cache = FakeSemanticCache()
        result = await process_row(
            spec_text=spec,
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=graph_engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert mm.has_matching_equivalence(spec, found) is False
        assert result.get("price") is None

    async def test_previously_confirmed_pair_accepted_without_review(self, graph_engine):
        """Ранее подтверждённая пара принимается без requires_review/капа confidence."""
        from src.memory_manager import MemoryManager

        mm = MemoryManager(graph_engine)
        spec = "Компенсатор сильфонный под приварку Ду40"
        found = "Компенсатор сильфонный осевой многослойный б/кожух"
        mm.record_matching_equivalence(spec, found)
        llm = FakeLLM([llm_tool_call("save_confirmed_price", {
            "product_name": found,
            "price": 5088.5, "confidence": 0.95,
            "url": "https://site.ru/i1/v2/", "site": "site.ru",
        })])
        bridge = FakeBridge()
        cache = FakeSemanticCache()
        result = await process_row(
            spec_text=spec,
            llm_client=llm,
            mcp_bridge=bridge,
            graph_engine=graph_engine,
            memory_manager=mm,
            fresh=True,
            semantic_cache=cache,
        )
        assert result.get("price") == 5088.5
        assert result.get("requires_review") is False
        assert result.get("confidence", 0) == 0.95


@pytest.mark.asyncio
class TestErrorResultContract:
    async def test_error_result_shape(self):
        err = _error_result("spec", "boom", elapsed=1.5)
        assert err["price"] is None
        assert err["error"] == "boom"
        assert err["elapsed"] == 1.5
        assert err["requires_review"] is True
