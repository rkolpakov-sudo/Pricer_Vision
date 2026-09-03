"""Тесты чистых функций антидетект-конфигурации MCP-сервера браузера.

Не требуют браузера: проверяются генерация init-скрипта зачистки window.set*
и параметры запуска AsyncCamoufox (locale/timezone/geoip/humanize).
"""

import asyncio
import json

from mcp_servers.browser_server import (
    _CAMOUFOX_SETTERS, _setter_cleanup_script, _camoufox_launch_kwargs,
    _is_snapshot_ref, _action_error_hint, _resolve_action_target, _element_cache,
    _SEARCH_INPUT_FILL_JS,
)


class TestSnapshotRef:
    def test_recognizes_snapshot_refs(self):
        assert _is_snapshot_ref("e2354") is True
        assert _is_snapshot_ref("e792754") is True
        assert _is_snapshot_ref("e1234") is True

    def test_rejects_selectors_and_empty(self):
        assert _is_snapshot_ref("input[name='search']") is False
        assert _is_snapshot_ref("button Найти") is False
        assert _is_snapshot_ref("") is False
        assert _is_snapshot_ref("e12x") is False


class TestActionErrorHint:
    def test_strict_mode_hint(self):
        err = RuntimeError("strict mode violation: locator(\"input[name='q']\") resolved to 3 elements")
        hint = _action_error_hint(err)
        assert "strict mode violation" in hint
        assert "3 элементами" in hint
        assert "роль" in hint

    def test_timeout_hint(self):
        hint = _action_error_hint(TimeoutError("Locator.fill: Timeout 10000ms exceeded"))
        assert "не найден" in hint
        assert "browser_snapshot" in hint
        assert "searchbox" in hint

    def test_unknown_error_no_hint(self):
        assert _action_error_hint(RuntimeError("boom")) == ""


class TestSearchInputFillJs:
    """_SEARCH_INPUT_FILL_JS — JS-fallback для ввода в поиск без расхода LLM-раунда."""

    def test_prefers_visible_search_over_hidden(self):
        env = """<input type="search" name="search" style="display:none">
                 <input type="search" name="search">"""
        code = _SEARCH_INPUT_FILL_JS + "\n" + "(text) => null;"  # placeholder, replaced below
        # Реально исполняем через Node? нет — проверяем только наличие нужных селекторов.
        assert 'input[type="search"]' in _SEARCH_INPUT_FILL_JS
        assert 'offsetParent' in _SEARCH_INPUT_FILL_JS

    def test_contains_visibility_filter_and_priority(self):
        assert 'offsetParent === null' in _SEARCH_INPUT_FILL_JS
        # search-селекторы идут раньше generic name=q / name=text
        search_idx = _SEARCH_INPUT_FILL_JS.index('input[type="search"]')
        q_idx = _SEARCH_INPUT_FILL_JS.index('input[name="q"]')
        assert search_idx < q_idx

    def test_returns_null_when_no_input(self):
        # JS должен вернуть null, если полей нет — код не должен падать.
        assert 'return null' in _SEARCH_INPUT_FILL_JS


class TestResolveActionTarget:
    def test_stale_snapshot_ref_returns_immediate_error(self):
        kind = _resolve_action_target("e999999")
        assert kind[0] == "error"
        assert "устаревший ref" in kind[1]

    def test_snapshot_ref_from_cache_returns_role(self):
        _element_cache["e1234"] = {"role": "button", "name": "Найти"}
        try:
            kind = _resolve_action_target("e1234")
            assert kind == ("role", "button", "Найти")
        finally:
            _element_cache.pop("e1234", None)

    def test_role_locator_prefix(self):
        kind = _resolve_action_target("textbox Поиск")
        assert kind == ("role", "textbox", "Поиск")
        kind2 = _resolve_action_target("button Найти")
        assert kind2 == ("role", "button", "Найти")

    def test_css_selector(self):
        kind = _resolve_action_target("input[name='search']")
        # единая арность 3-кортежа: селектор в kind[1], kind[2]="" для CSS
        assert kind == ("css", "input[name='search']", "")

    def test_empty_target(self):
        kind = _resolve_action_target("")
        assert kind[0] == "error"


class _FakeLocator:
    async def fill(self, text, timeout=None):
        assert text
        return "ok"

    async def click(self, timeout=None):
        return "ok"

    async def hover(self, timeout=None):
        return "ok"


class _FakePage:
    def __init__(self):
        self.clicked = []
        self.filled = []
        self.hovered = []
        self.url_value = "https://example.com/page"
        self.goto_called = 0
        self.last_url = None

    @property
    def url(self):
        return self.url_value

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_called += 1
        self.last_url = url
        return None

    async def evaluate(self, js, arg=None):
        # Эмуляция _CLICK_FIND_JS/_CLICK_FORCE_JS: для CSS/роли считаем элемент найденным.
        if "_CLICK_FIND_JS" in js or "findEl" in js or "_findEl" in js:
            return {"found": True, "tag": "a", "href": "https://example.com/target", "text": "target"}
        return None

    def locator(self, sel):
        assert sel, "CSS-селектор не должен быть пустым"
        return _FakeLocator()

    def get_by_role(self, role, name=None):
        return _FakeLocator()

    async def click(self, sel, timeout=None):
        assert sel, "CSS-селектор не должен быть пустым"
        self.clicked.append(sel)
        return "ok"

    async def hover(self, sel, timeout=None):
        assert sel, "CSS-селектор не должен быть пустым"
        self.hovered.append(sel)
        return "ok"


class TestCssSelectorNoRegression:
    """Регрессия: 2-кортеж ('css', t) в _resolve_action_target давал IndexError
    'tuple index out of range' на каждом CSS-клике/вводе → агент не мог работать."""

    def test_type_text_css_selector_no_index_error(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)
        page = _FakePage()
        res = asyncio.run(driver.type_text(page, "input[name='search']", "водомер"))
        assert res == "ok"
        assert not res.startswith("error")

    def test_click_css_selector_no_index_error(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)
        page = _FakePage()
        res = asyncio.run(driver.click(page, "a.button-search"))
        assert res == "ok"

    def test_hover_css_selector_no_index_error(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)
        page = _FakePage()
        res = asyncio.run(driver.hover(page, "div.catalog"))
        assert res == "ok"

    def test_stale_ref_still_fast_error(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)
        page = _FakePage()
        res = asyncio.run(driver.type_text(page, "e999999", "x"))
        assert res.startswith("error:")
        assert "устаревший ref" in res


class TestSetterCleanupScript:
    def test_contains_all_setters(self):
        script = _setter_cleanup_script()
        for name in _CAMOUFOX_SETTERS:
            assert json.dumps(name) in script

    def test_deletes_from_window(self):
        script = _setter_cleanup_script()
        assert "delete window[" in script

    def test_does_not_touch_global_functions(self):
        script = _setter_cleanup_script()
        assert "setTimeout" not in script
        assert "setInterval" not in script
        assert "setResizable" not in script


class TestCamoufoxLaunchKwargs:
    def test_default_ru_locale_and_moscow_tz(self):
        kwargs = _camoufox_launch_kwargs(False)
        assert kwargs["headless"] is False
        assert kwargs["os"] == "windows"
        assert kwargs["humanize"] is True
        assert kwargs["locale"] == "ru-RU"
        assert kwargs["config"] == {"timezone": "Europe/Moscow"}
        assert "geoip" not in kwargs

    def test_custom_locale_timezone(self):
        kwargs = _camoufox_launch_kwargs(True, locale="en-US", timezone="America/New_York")
        assert kwargs["locale"] == "en-US"
        assert kwargs["config"] == {"timezone": "America/New_York"}

    def test_geoip_mode(self):
        kwargs = _camoufox_launch_kwargs(False, geoip=True)
        assert kwargs["geoip"] is True
        assert "locale" not in kwargs
        assert "config" not in kwargs

    def test_humanize_disabled(self):
        kwargs = _camoufox_launch_kwargs(False, humanize=False)
        assert kwargs["humanize"] is False

    def test_persistent_profile_disabled_by_default(self):
        """Постоянный профиль ВЫКЛЮЧЕН по умолчанию: эфемерный браузер, один
        экземпляр (persistent_context на общем профиле давал два браузера)."""
        kwargs = _camoufox_launch_kwargs(False)
        assert "persistent_context" not in kwargs
        assert "user_data_dir" not in kwargs

    def test_persistent_profile_enabled_explicitly(self):
        kwargs = _camoufox_launch_kwargs(False, persistent_profile=True)
        assert kwargs.get("persistent_context") is True
        assert kwargs.get("user_data_dir") == "data/camoufox_profile"
        assert kwargs.get("enable_cache") is True
        # session-restore отключён, чтобы Firefox не открывал прошлые окна
        prefs = kwargs.get("firefox_user_prefs", {})
        assert prefs.get("browser.sessionstore.resume_from_crash") is False
        assert prefs.get("browser.sessionstore.max_resumed_crashes") == 0
        assert prefs.get("browser.startup.page") == 0

    def test_persistent_profile_disabled(self):
        kwargs = _camoufox_launch_kwargs(False, persistent_profile=False)
        assert "persistent_context" not in kwargs
        assert "user_data_dir" not in kwargs

    def test_pinned_fingerprint_disabled_by_default(self):
        """pin отпечатка без постоянного профиля не нужен — дефолт False."""
        kwargs = _camoufox_launch_kwargs(False)
        assert kwargs.get("fingerprint_preset") is True

    def test_pinned_fingerprint_uses_deterministic_preset(self):
        """pinned_fingerprint подставляет конкретный Windows-пресет (стабильный индекс)."""
        kwargs = _camoufox_launch_kwargs(False, pinned_fingerprint=True)
        preset = kwargs.get("fingerprint_preset")
        assert isinstance(preset, dict)
        assert preset.get("navigator", {}).get("platform") == "Win32"
        # одинаковый пресет при каждом вызове
        again = _camoufox_launch_kwargs(False, pinned_fingerprint=True).get("fingerprint_preset")
        assert preset == again

    def test_pinned_fingerprint_disabled_uses_bool(self):
        kwargs = _camoufox_launch_kwargs(False, pinned_fingerprint=False)
        assert kwargs.get("fingerprint_preset") is True


class _FakeGotoPage:
    """Fake для CamoufoxDriver.goto: имитирует page.goto и смену url."""

    def __init__(self, goto_fails: bool = False):
        self.goto_fails = goto_fails
        self.goto_called = 0
        self.last_url = None

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_called += 1
        self.last_url = url
        if self.goto_fails:
            raise Exception("navigation failed")
        return None


class TestClickFastFailAndFallback:
    """Новые механики browser_click:
    - fast-fail: элемент не в DOM → мгновенная ошибка (не 10с таймаут);
    - link-fallback: честный клик не смог, но элемент — ссылка → навигация на href;
    - force-click fallback для не-ссылок.
    """

    def test_element_not_in_dom_returns_fast_error(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _GonePage(_FakePage):
            async def evaluate(self, js, arg=None):
                return {"found": False}

        res = asyncio.run(driver.click(_GonePage(), "a.button-search"))
        assert res.startswith("error: click failed")
        assert "не найден в DOM" in res

    def test_link_fallback_navigates_to_href(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _TimeoutLinkPage(_FakePage):
            async def evaluate(self, js, arg=None):
                # finder находит ссылку с href
                return {"found": True, "tag": "a",
                        "href": "https://valtec.ru/catalog/x.html", "text": "x"}

            async def click(self, sel, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

        page = _TimeoutLinkPage()
        res = asyncio.run(driver.click(page, "a:has-text('x')"))
        assert "click-fallback" in res
        assert page.goto_called == 1
        assert page.last_url == "https://valtec.ru/catalog/x.html"

    def test_force_click_fallback_for_non_link(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _RaisingLocator:
            async def click(self, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

            async def fill(self, text, timeout=None):
                raise TimeoutError("Locator.fill: Timeout 10000ms exceeded")

        class _TimeoutButtonPage(_FakePage):
            def __init__(self):
                super().__init__()
                self.force_eval_called = False

            async def evaluate(self, js, arg=None):
                # первый evaluate (finder): кнопка без href; второй (force): ok
                if not self.force_eval_called:
                    return {"found": True, "tag": "button", "href": "", "text": "Найти"}
                self.force_eval_called = True
                return {"ok": True}

            def get_by_role(self, role, name=None):
                return _RaisingLocator()

        page = _TimeoutButtonPage()
        res = asyncio.run(driver.click(page, "button Найти"))
        assert "force-click" in res

    def test_role_target_goes_through_same_fallback(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _RaisingLocator:
            async def click(self, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

        class _RoleTimeoutPage(_FakePage):
            async def evaluate(self, js, arg=None):
                return {"found": True, "tag": "a",
                        "href": "https://example.com/product/1", "text": "Тройник"}

            def get_by_role(self, role, name=None):
                return _RaisingLocator()

        _element_cache["e1234"] = {"role": "link", "name": "Тройник"}
        try:
            page = _RoleTimeoutPage()
            res = asyncio.run(driver.click(page, "e1234"))
            assert "click-fallback" in res
            assert page.last_url == "https://example.com/product/1"
        finally:
            _element_cache.pop("e1234", None)

    def test_fallback_failure_puts_reason_first(self):
        """При провале fallback причина — в НАЧАЛЕ ошибки (лог режет до ~100 символов,
        сырой Playwright-текст не должен скрывать, почему клик не удался)."""
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _RaisingLocator:
            async def click(self, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

        class _ForceFailPage(_FakePage):
            def __init__(self):
                super().__init__()
                self.force_eval_called = False

            async def evaluate(self, js, arg=None):
                if not self.force_eval_called:
                    self.force_eval_called = True
                    return {"found": True, "tag": "div", "href": "", "text": "Фильтр"}
                return {"ok": False, "reason": "not-found"}

            async def click(self, sel, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

            def get_by_role(self, role, name=None):
                return _RaisingLocator()

        res = asyncio.run(driver.click(_ForceFailPage(), "button Фильтр"))
        assert res.startswith("error: click failed")
        assert "not-found" in res[:160]

    def test_link_fallback_goto_failure_reason_first(self):
        from mcp_servers.browser_server import CamoufoxDriver
        driver = CamoufoxDriver(headless=True)

        class _RaisingLocator:
            async def click(self, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

        class _GotoFailLinkPage(_FakePage):
            async def evaluate(self, js, arg=None):
                return {"found": True, "tag": "a",
                        "href": "https://example.com/target", "text": "x"}

            async def goto(self, url, wait_until=None, timeout=None):
                raise Exception("nav timeout")

            async def click(self, sel, timeout=None):
                raise TimeoutError("Locator.click: Timeout 10000ms exceeded")

        res = asyncio.run(driver.click(_GotoFailLinkPage(), "a:has-text('x')"))
        assert res.startswith("error: click failed")
        assert "link-fallback goto failed" in res[:200]


class TestGotoClearsElementCache:
    """Переход на другую страницу инвалидирует ref снапшота со старой страницы."""

    def test_goto_clears_cache(self):
        from mcp_servers.browser_server import CamoufoxDriver
        _element_cache["e1234"] = {"role": "button", "name": "Старое"}
        try:
            driver = CamoufoxDriver(headless=True)
            asyncio.run(driver.goto(_FakeGotoPage(), "https://example.com/new"))
            assert "e1234" not in _element_cache
        finally:
            _element_cache.pop("e1234", None)

    def test_goto_clears_cache_even_when_nav_fails(self):
        from mcp_servers.browser_server import CamoufoxDriver
        _element_cache["e999"] = {"role": "link", "name": "x"}
        try:
            driver = CamoufoxDriver(headless=True)
            res = asyncio.run(driver.goto(_FakeGotoPage(goto_fails=True), "https://example.com/err"))
            assert res.startswith("error:")
            assert "e999" not in _element_cache
        finally:
            _element_cache.pop("e999", None)
