"""Тесты чистых функций антидетект-конфигурации MCP-сервера браузера.

Не требуют браузера: проверяются генерация init-скрипта зачистки window.set*
и параметры запуска AsyncCamoufox (locale/timezone/geoip/humanize).
"""

import asyncio
import json

from mcp_servers.browser_server import (
    _CAMOUFOX_SETTERS, _setter_cleanup_script, _camoufox_launch_kwargs,
    _is_snapshot_ref, _action_error_hint, _resolve_action_target, _element_cache,
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

    def test_unknown_error_no_hint(self):
        assert _action_error_hint(RuntimeError("boom")) == ""


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
