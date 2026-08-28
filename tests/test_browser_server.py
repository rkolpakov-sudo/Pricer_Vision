"""Тесты чистых функций антидетект-конфигурации MCP-сервера браузера.

Не требуют браузера: проверяются генерация init-скрипта зачистки window.set*
и параметры запуска AsyncCamoufox (locale/timezone/geoip/humanize).
"""

import json

from mcp_servers.browser_server import (
    _CAMOUFOX_SETTERS, _setter_cleanup_script, _camoufox_launch_kwargs,
)


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

    def test_persistent_profile_enabled_by_default(self):
        """Постоянный профиль включён по умолчанию: куки между сессиями сохраняются."""
        kwargs = _camoufox_launch_kwargs(False)
        assert kwargs.get("persistent_context") is True
        assert kwargs.get("user_data_dir") == "data/camoufox_profile"
        assert kwargs.get("enable_cache") is True

    def test_persistent_profile_disabled(self):
        kwargs = _camoufox_launch_kwargs(False, persistent_profile=False)
        assert "persistent_context" not in kwargs
        assert "user_data_dir" not in kwargs

    def test_pinned_fingerprint_uses_deterministic_preset(self):
        """pinned_fingerprint подставляет конкретный Windows-пресет (стабильный индекс)."""
        kwargs = _camoufox_launch_kwargs(False)
        preset = kwargs.get("fingerprint_preset")
        assert isinstance(preset, dict)
        assert preset.get("navigator", {}).get("platform") == "Win32"
        # одинаковый пресет при каждом вызове
        again = _camoufox_launch_kwargs(False).get("fingerprint_preset")
        assert preset == again

    def test_pinned_fingerprint_disabled_uses_bool(self):
        kwargs = _camoufox_launch_kwargs(False, pinned_fingerprint=False)
        assert kwargs.get("fingerprint_preset") is True
