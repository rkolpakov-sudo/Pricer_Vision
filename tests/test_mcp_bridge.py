import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import pytest
from src.mcp_bridge import (
    MCPBridge, _sanitize_js, _is_url, resolve_backends,
    _is_strict_mode_error, _extract_strict_selector, _strict_inventory_js,
    _fields_inventory_js, _is_css_parse_error, _is_fill_timeout, _is_click_timeout,
    _role_locator_to_css, _strict_mode_hint, _fill_timeout_hint,
    _css_parse_hint, _click_timeout_hint,
)
from src.resilience import mcp_circuit


class TestSanitizeJs:
    def test_trailing_comment_swallowing_brace(self):
        js = "() => { const p = document.querySelector('.x'); return p.textContent.trim(); // done }"
        out = _sanitize_js(js)
        assert out.count("{") == out.count("}")
        assert "// done" not in out
        assert out.endswith("}")

    def test_unclosed_arrow_function_gets_closed(self):
        js = "() => { const priceBlock = document.querySelector('[class*=\"price\"], .product-price-block'); if (priceBlock) return priceBlock.textContent.trim();"
        out = _sanitize_js(js)
        assert out.count("{") == out.count("}")
        assert out.endswith("}")

    def test_url_inside_string_not_stripped(self):
        js = "() => { const u = 'https://www.santech.ru/catalog/'; return u; }"
        assert _sanitize_js(js) == js

    def test_valid_js_untouched(self):
        js = "() => { const a = 1; const b = 2; return a + b; }"
        assert _sanitize_js(js) == js

    def test_multi_line_with_inline_comment(self):
        js = "() => {\n  // find the price block\n  const p = document.querySelector('.price');\n  if (p) return p.textContent;\n}"
        assert _sanitize_js(js) == js

    def test_empty(self):
        assert _sanitize_js("") == ""
        assert _sanitize_js(None) is None


class TestIsUrl:
    def test_urls(self):
        assert _is_url("https://www.santech.ru/catalog/293/306/i46584/v155997/")
        assert _is_url("http://example.com")
        assert not _is_url("e83")
        assert not _is_url("textbox 'Поиск'")
        assert not _is_url("")


class TestMCPBridge:
    def test_initial_state(self):
        bridge = MCPBridge()
        assert bridge._servers == []
        assert bridge._stopped is False
        assert bridge._lock is not None

    @pytest.mark.asyncio
    async def test_call_tool_before_start(self):
        bridge = MCPBridge()
        result = await bridge.call_tool("navigate", {"url": "http://example.com"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_health_check_before_start(self):
        bridge = MCPBridge()
        assert await bridge.health_check() is False

    @pytest.mark.asyncio
    async def test_restart_stops_and_returns_bool(self):
        bridge = MCPBridge()
        ok = await bridge.restart()
        assert isinstance(ok, bool)

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        bridge = MCPBridge()
        await bridge.stop()
        assert bridge._stopped is True
        await bridge.stop()
        assert bridge._stopped is True

    def test_lock_exists(self):
        bridge = MCPBridge()
        assert hasattr(bridge, "_lock")

    @pytest.mark.asyncio
    async def test_start_fails_gracefully_when_no_server(self):
        bridge = MCPBridge()
        ok = await bridge.start()
        assert isinstance(ok, bool)

    @pytest.mark.asyncio
    async def test_set_backend_unknown_rejected(self):
        bridge = MCPBridge()
        assert await bridge.set_backend("bogus") is False

    @pytest.mark.asyncio
    async def test_set_backend_valid_sets_override(self):
        bridge = MCPBridge()
        assert await bridge.set_backend("nodriver") is True
        assert bridge._backend_override == "nodriver"

    @pytest.mark.asyncio
    async def test_start_resets_stopped_after_failover(self, monkeypatch):
        bridge = MCPBridge()

        async def fake_start_one(backend):
            if backend == "camoufox":
                return False
            srv = SimpleNamespace(session=object(), tools=[], name=backend)
            bridge._servers.append(srv)
            bridge._tool_map["browser_navigate"] = srv
            return True

        monkeypatch.setattr(bridge, "_start_one", fake_start_one)
        monkeypatch.setattr("src.mcp_bridge.resolve_backends",
                            lambda: ["camoufox", "playwright", "nodriver"])
        ok = await bridge.start()
        assert ok is True
        assert bridge._backend == "playwright"
        assert bridge._stopped is False
        assert "browser_navigate" in bridge._tool_map


@pytest.mark.asyncio
async def test_concurrent_call_tool_returns_error():
    b1 = MCPBridge()
    b2 = MCPBridge()
    r1, r2 = await asyncio.gather(
        b1.call_tool("snapshot", {}),
        b2.call_tool("snapshot", {}),
    )
    assert "error" in r1
    assert "error" in r2


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, text="ok"):
        self.content = [_FakeContent(text)]


class _FakeServer:
    def __init__(self):
        self.name = "playwright"
        self.session = AsyncMock()
        self.session.call_tool = AsyncMock(return_value=_FakeResult("ok"))


@pytest.mark.asyncio
async def test_click_url_rewrites_to_navigate():
    mcp_circuit.reset()
    bridge = MCPBridge()
    srv = _FakeServer()
    bridge._tool_map["browser_click"] = srv
    bridge._tool_map["browser_navigate"] = srv
    url = "https://www.santech.ru/catalog/293/306/i46584/v155997/"
    await bridge.call_tool("browser_click", {"target": url})
    call = srv.session.call_tool.await_args
    assert call.args[0] == "browser_navigate"
    assert call.args[1] == {"url": url}


@pytest.mark.asyncio
async def test_evaluate_js_is_sanitized():
    mcp_circuit.reset()
    bridge = MCPBridge()
    srv = _FakeServer()
    bridge._tool_map["browser_evaluate"] = srv
    broken = "() => { const p = document.querySelector('.x'); return p.textContent.trim(); // }"
    await bridge.call_tool("browser_evaluate", {"function": broken})
    call = srv.session.call_tool.await_args
    fn = call.args[1]["function"]
    assert fn.count("{") == fn.count("}")
    assert fn.endswith("}")


class TestMCPCallTimeout:
    @staticmethod
    def _hanging_server():
        async def _hang(*a, **k):
            await asyncio.sleep(100)
            return _FakeResult("ok")

        srv = _FakeServer()
        srv.session.call_tool = AsyncMock(side_effect=_hang)
        return srv

    @pytest.mark.asyncio
    async def test_call_tool_times_out_and_returns_error(self):
        mcp_circuit.reset()
        bridge = MCPBridge(call_timeout=0.05, restart_after_timeouts=5)
        bridge._tool_map["browser_snapshot"] = self._hanging_server()
        result = await bridge.call_tool("browser_snapshot", {})
        assert result.startswith("error: tool call timed out")

    @pytest.mark.asyncio
    async def test_repeated_timeouts_trigger_restart(self):
        mcp_circuit.reset()
        bridge = MCPBridge(call_timeout=0.05, restart_after_timeouts=2)
        bridge._tool_map["browser_snapshot"] = self._hanging_server()
        restarted = AsyncMock()
        bridge._restart_safe = restarted
        await bridge.call_tool("browser_snapshot", {})
        await bridge.call_tool("browser_snapshot", {})
        restarted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_resets_timeout_counter(self):
        mcp_circuit.reset()
        bridge = MCPBridge(call_timeout=0.05, restart_after_timeouts=2)
        hanging = self._hanging_server()
        bridge._tool_map["browser_snapshot"] = hanging
        restarted = AsyncMock()
        bridge._restart_safe = restarted
        await bridge.call_tool("browser_snapshot", {})  # timeout #1
        assert bridge._consecutive_timeouts == 1
        bridge._tool_map["browser_snapshot"] = _FakeServer()
        assert await bridge.call_tool("browser_snapshot", {}) == "ok"
        assert bridge._consecutive_timeouts == 0
        bridge._tool_map["browser_snapshot"] = hanging
        await bridge.call_tool("browser_snapshot", {})  # timeout #1 again
        assert bridge._consecutive_timeouts == 1
        restarted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plain_failure_not_counted_as_timeout(self):
        mcp_circuit.reset()
        bridge = MCPBridge(call_timeout=0.05, restart_after_timeouts=2)
        srv = _FakeServer()
        srv.session.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        bridge._tool_map["browser_snapshot"] = srv
        result = await bridge.call_tool("browser_snapshot", {})
        assert "error" in result
        assert bridge._consecutive_timeouts == 0


class _FakeMCPTool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.inputSchema = {"type": "object", "properties": {}}


class TestPlaywrightErrorFeedback:
    """Чистые помощники Фазы 2: детекция и перевод ошибок Playwright."""

    def test_strict_mode_detection(self):
        err = 'error: type failed: Locator.fill: Error: strict mode violation: locator("input[name=\\"search\\"]") resolved to 2 elements'
        assert _is_strict_mode_error(err) is True
        assert _extract_strict_selector(err) == 'input[name="search"]'

    def test_strict_selector_none_when_not_present(self):
        assert _extract_strict_selector("no such pattern") is None

    def test_strict_inventory_js_embeds_selector_safely(self):
        js = _strict_inventory_js('input[name="search"]')
        # JS-строка должна быть валидным литералом (json.dumps)
        parsed = json.loads(js.split("const sel = ")[1].split(";")[0])
        assert parsed == 'input[name="search"]'
        assert "offsetWidth" in js

    def test_fields_inventory_js(self):
        js = _fields_inventory_js()
        assert "input,textarea,select" in js
        assert "placeholder" in js

    def test_css_parse_detection(self):
        err = 'error: type failed: Locator.fill: Unexpected token "get_by_role(" while parsing css selector'
        assert _is_css_parse_error(err) is True
        assert _is_css_parse_error("ordinary error") is False

    def test_fill_timeout_detection(self):
        err = "error: type failed: Locator.fill: Timeout 10000ms exceeded. Call log: - waiting for locator"
        assert _is_fill_timeout(err) is True
        assert _is_fill_timeout("error: click failed: Page.click: Timeout") is False

    def test_click_timeout_detection(self):
        err = "error: click failed: Page.click: Timeout 10000ms exceeded."
        assert _is_click_timeout(err) is True
        assert _is_click_timeout("error: type failed: Locator.fill: Timeout") is False

    def test_role_locator_button_translation(self):
        assert _role_locator_to_css('get_by_role("button", name="Найти")') == 'button:has-text("Найти")'
        assert _role_locator_to_css("button \"Найти\"") == 'button:has-text("Найти")'

    def test_role_locator_link_translation(self):
        assert _role_locator_to_css('getByRole("link", {name: "товар"})') == 'a:has-text("товар")'

    def test_role_locator_textbox_not_translated(self):
        assert _role_locator_to_css('get_by_role("textbox", name="Поиск")') is None
        assert _role_locator_to_css('textbox "Поиск"') is None
        assert _role_locator_to_css("") is None

    def test_hint_builders_contain_guidance(self):
        assert "селектор" in _strict_mode_hint("err", "[inventory]").lower()
        assert "Доступные поля" in _fill_timeout_hint("err", "[inventory]")
        assert "button:has-text" in _css_parse_hint("err", 'button:has-text("Найти")')
        assert "browser_snapshot" in _click_timeout_hint("err")


class _InventoryServer:
    """Фейковый сервер: на browser_evaluate отвечает инвентарём."""

    def __init__(self, inventory="[{\"idx\":0,\"visible\":true,\"name\":\"search\"}]"):
        self.name = "playwright"
        self.session = AsyncMock()
        self.session.call_tool = AsyncMock(
            return_value=_FakeResult(inventory)
        )


class TestEnhanceError:
    @pytest.mark.asyncio
    async def test_non_error_unchanged(self):
        bridge = MCPBridge()
        srv = _InventoryServer()
        out = await bridge._enhance_error(srv, "browser_snapshot", {}, "ok")
        assert out == "ok"

    @pytest.mark.asyncio
    async def test_strict_mode_returns_inventory(self):
        bridge = MCPBridge()
        srv = _InventoryServer(inventory='[{"idx":0,"visible":true,"name":"search"}]')
        err = 'error: type failed: Locator.fill: Error: strict mode violation: locator("input[name=\\"search\\"]") resolved to 2 elements'
        out = await bridge._enhance_error(srv, "browser_type", {}, err)
        assert "совпал с несколькими элементами" in out
        assert "search" in out

    @pytest.mark.asyncio
    async def test_strict_mode_without_selector_unchanged(self):
        bridge = MCPBridge()
        srv = _InventoryServer()
        out = await bridge._enhance_error(srv, "browser_type", {}, "error: strict mode violation (no selector)")
        assert out == "error: strict mode violation (no selector)"

    @pytest.mark.asyncio
    async def test_css_parse_returns_hint_with_translation(self):
        bridge = MCPBridge()
        srv = _InventoryServer()
        err = 'error: type failed: Locator.fill: Unexpected token "get_by_role(" while parsing css selector'
        out = await bridge._enhance_error(srv, "browser_type", {"target": 'get_by_role("button", name="Найти")'}, err)
        assert "button:has-text" in out
        assert "Playwright-локатор" in out

    @pytest.mark.asyncio
    async def test_fill_timeout_returns_field_inventory(self):
        bridge = MCPBridge()
        srv = _InventoryServer(inventory='[{"idx":0,"tag":"INPUT","ph":"Что вы ищете?","visible":true}]')
        err = "error: type failed: Locator.fill: Timeout 10000ms exceeded. Call log: - waiting for locator"
        out = await bridge._enhance_error(srv, "browser_type", {"target": "input[name=search]"}, err)
        assert "Доступные поля" in out
        assert "Что вы ищете?" in out

    @pytest.mark.asyncio
    async def test_click_timeout_returns_snapshot_hint(self):
        bridge = MCPBridge()
        srv = _InventoryServer()
        err = "error: click failed: Page.click: Timeout 10000ms exceeded. Call log: - waiting for locator(\"e553100\")"
        out = await bridge._enhance_error(srv, "browser_click", {"target": "e553100"}, err)
        assert "browser_snapshot" in out

    @pytest.mark.asyncio
    async def test_inventory_failure_falls_back_to_original(self):
        bridge = MCPBridge()
        srv = _InventoryServer()
        srv.session.call_tool = AsyncMock(side_effect=RuntimeError("browser down"))
        err = "error: type failed: Locator.fill: Timeout 10000ms exceeded."
        out = await bridge._enhance_error(srv, "browser_type", {}, err)
        assert out == err


class _FakeMCPSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=[_FakeMCPTool("browser_navigate")])


@pytest.mark.asyncio
async def test_start_passes_headless_flag(monkeypatch):
    captured = {}

    class _FakeStdioCtx:
        def __init__(self, args):
            self._args = args

        async def __aenter__(self):
            captured["args"] = list(self._args)
            return object(), object()

        async def __aexit__(self, *a):
            return False

    def fake_stdio(params):
        return _FakeStdioCtx(params.args)

    def fake_client(read, write):
        return _FakeMCPSession()

    import src.mcp_bridge as mb
    monkeypatch.setattr(mb, "stdio_client", fake_stdio)
    monkeypatch.setattr(mb, "ClientSession", fake_client)

    bridge = MCPBridge(headless=True)
    assert await bridge.start() is True
    assert "--headless" in captured["args"]

    captured.clear()
    bridge2 = MCPBridge(headless=False)
    assert await bridge2.start() is True
    assert "--headless" not in captured["args"]
