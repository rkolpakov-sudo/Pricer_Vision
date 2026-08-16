import pytest
from src.agent_loop import (
    GRAPH_TOOL_NAMES, GRAPH_TOOL_DEFS, SYSTEM_PROMPT,
    _build_context, _execute_graph_tool,
    _error_result, _result_to_schema,
    _estimate_tokens, _trim_messages_for_budget,
    CONTEXT_TOKEN_BUDGET,
    TEMP_EXPLORATION, TEMP_NAVIGATION, TEMP_EXTRACTION, TEMP_RECOVERY,
)


class TestConstants:
    def test_graph_tools_defined(self):
        assert "get_approaches" in GRAPH_TOOL_NAMES
        assert "save_approach" in GRAPH_TOOL_NAMES
        assert "get_confirmed_prices" in GRAPH_TOOL_NAMES
        assert "save_confirmed_price" in GRAPH_TOOL_NAMES
        assert "search_sites" in GRAPH_TOOL_NAMES
        assert "save_discovered_site" in GRAPH_TOOL_NAMES
        assert "get_hints" in GRAPH_TOOL_NAMES
        assert len(GRAPH_TOOL_NAMES) == 7

    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 50

    def test_tool_defs_have_descriptions(self):
        for td in GRAPH_TOOL_DEFS:
            assert len(td["function"]["description"]) > 5

    def test_phase_temperatures_distinct(self):
        temps = {TEMP_EXPLORATION, TEMP_NAVIGATION, TEMP_EXTRACTION, TEMP_RECOVERY}
        assert len(temps) == 4
        assert TEMP_EXTRACTION == 0.1
        assert TEMP_EXPLORATION == 0.7


class TestContextBudget:
    def test_budget_constant(self):
        assert CONTEXT_TOKEN_BUDGET == 8000

    def test_estimate_tokens(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("short") == 1
        assert _estimate_tokens("a" * 40) == 10

    def test_trim_keeps_small_messages(self):
        messages = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": "hello world"}
        ]
        out = _trim_messages_for_budget(messages)
        assert out == messages

    def test_trim_removes_old_when_over_budget(self):
        big = "x" * 40000  # ~10000 tokens, over budget alone
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "final"},
        ]
        out = _trim_messages_for_budget(messages)
        # последний user + system сохраняются
        roles = [m["role"] for m in out]
        assert roles[-1] == "user"
        assert roles[0] == "system"
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= CONTEXT_TOKEN_BUDGET

    def test_trim_preserves_last_user(self):
        messages = [{"role": "system", "content": "s"}]
        for i in range(50):
            messages.append({"role": "user", "content": "m" * 1000})
        messages.append({"role": "assistant", "content": "tail"})
        out = _trim_messages_for_budget(messages)
        assert out[-1]["content"] == "tail"
        assert any(m["role"] == "user" for m in out)


class TestBuildContext:
    def test_minimal(self):
        ctx = _build_context("test item", "unknown", [], [], [], [])
        assert "test item" in ctx

    def test_with_product_type(self):
        ctx = _build_context("ВВГ-нг", "cables", [], [], [], [],
                             product_data={"name": "Кабели", "category": "cables"})
        assert "cables" in ctx

    def test_with_approaches(self):
        approaches = [{"site_id": "tinko.ru", "success_count": 5, "method": "search_then_navigate"}]
        ctx = _build_context("test", "cables", approaches, [], [], [],
                             product_data={"name": "cables"})
        assert "tinko.ru" in ctx
        assert "успехов=5" in ctx or "5" in ctx

    def test_with_confirmed_prices(self):
        prices = [{"spec_text": "ВВГ 3x1.5", "price": 1500.5, "site_id": "tinko.ru", "confidence": 0.95}]
        ctx = _build_context("test", "cables", [], prices, [], [],
                             product_data={"name": "cables"})
        assert "1500.5" in ctx or "1500" in ctx

    def test_with_sites(self):
        sites = [{"id": "tinko.ru"}, {"id": "keaz.ru"}]
        approaches = [{"site_id": "tinko.ru"}, {"site_id": "keaz.ru"}]
        ctx = _build_context("test", "cables", approaches, [], sites, [],
                             product_data={"name": "cables"})
        assert "tinko.ru" in ctx
        assert "keaz.ru" in ctx

    def test_hints_not_in_context(self):
        hints = [{"hint_text": "Искать в каталоге"}]
        ctx = _build_context("test", "cables", [], [], [], hints,
                             product_data={"name": "cables"})
        assert "Искать в каталоге" not in ctx

    def test_no_sites_message(self):
        ctx = _build_context("test", "unknown", [], [], [], [])
        assert "Yandex" in ctx or "известных сайтов нет" in ctx


class TestErrorResult:
    def test_returns_error_dict(self):
        r = _error_result("test spec", "something broke")
        assert r["spec_text"] == "test spec"
        assert r["price"] is None
        assert r["error"] == "something broke"
        assert r["requires_review"] is True


class TestExecuteGraphTool:
    def test_unknown_tool(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("nonexistent_tool", {}, graph_engine, mm)
        assert "error" in result

    def test_get_approaches_empty(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("get_approaches", {"product_type": "cables"}, graph_engine, mm)
        assert "Нет" in result

    def test_search_sites_empty(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("search_sites", {"product_type": "cables"}, graph_engine, mm)
        assert "Нет" in result

    def test_save_discovered_site(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        result = _execute_graph_tool("save_discovered_site", {
            "domain": "new-site.ru", "name": "New Site", "product_type": "cables"
        }, graph_engine, mm)
        assert "сохранён" in result.lower()
        sites = mm.get_sites("cables")
        assert any(s["id"] == "new-site.ru" for s in sites)


class TestResultToSchema:
    def test_success_result(self):
        result = {
            "spec_text": "ВВГ 3x1.5", "product_type": "cables",
            "price": 1500.5, "confidence": 0.95, "url": "https://tinko.ru",
            "site": "tinko.ru", "reason": "", "requires_review": False,
            "elapsed": 12.3,
        }
        out = _result_to_schema(result)
        assert out["spec_text"] == "ВВГ 3x1.5"
        assert out["found"] is True
        assert out["price"] == 1500.5
        assert out["requires_review"] is False
        assert out["product_type"] == "cables"

    def test_no_price_result(self):
        result = {
            "spec_text": "Кабель", "price": None, "confidence": 0.0,
            "requires_review": True, "error": "Max rounds reached", "elapsed": 30.0,
        }
        out = _result_to_schema(result)
        assert out["found"] is False
        assert out["price"] is None
        assert out["error"] == "Max rounds reached"

    def test_returns_original_on_invalid(self):
        result = {"spec_text": "", "price": -5, "requires_review": True}
        out = _result_to_schema(result)
        assert out is result
