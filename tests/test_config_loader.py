"""Тесты config_loader: чтение/сохранение конфига без изменения реального settings.yaml."""

import yaml

import src.config_loader as cl


def _redirect_config(tmp_path, monkeypatch, cfg):
    """Перенаправляет путь конфига на tmp_path и задаёт кэш."""
    monkeypatch.setattr(cl, "_SETTINGS_CACHE", cfg)
    monkeypatch.setattr(cl.os.path, "dirname", lambda p: str(tmp_path / "proj"))


class TestLoadSettings:
    def test_loads_dict_and_caches(self, monkeypatch):
        monkeypatch.setattr(cl, "_SETTINGS_CACHE", None)
        cfg = cl.load_settings()
        assert isinstance(cfg, dict)
        assert cl.load_settings() is cfg

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cl, "_SETTINGS_CACHE", None)
        monkeypatch.setattr(cl.os.path, "dirname", lambda p: str(tmp_path / "proj"))
        assert cl.load_settings() == {}

    def test_reload_ignores_cache(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        target.write_text("run:\n  max_rounds: 7\n", encoding="utf-8")
        monkeypatch.setattr(cl, "_SETTINGS_CACHE", {"run": {"max_rounds": 1}})
        monkeypatch.setattr(cl.os.path, "dirname", lambda p: str(tmp_path / "proj"))
        assert cl.load_settings(reload=True)["run"]["max_rounds"] == 7


class TestGetters:
    def test_get_run_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"run": {"max_rounds": 42}})
        assert cl.get_run_config("max_rounds", 1) == 42
        assert cl.get_run_config("missing", "d") == "d"

    def test_get_price_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"price": {"stale_days": 30}})
        assert cl.get_price_config("stale_days", 0) == 30
        assert cl.get_price_config("missing", "d") == "d"

    def test_get_llm_retry_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"llm": {"retry": {"max_attempts": 2}}})
        assert cl.get_llm_retry_config("max_attempts", 0) == 2
        assert cl.get_llm_retry_config("missing", "d") == "d"

    def test_get_antidetect_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"antidetect": {"rate_limit_min_interval": 1.5}})
        assert cl.get_antidetect_config("rate_limit_min_interval", 0.0) == 1.5
        assert cl.get_antidetect_config("missing", "d") == "d"

    def test_get_browser_config_antidetect_keys(self, monkeypatch):
        """Новые ключи антибот-консистентности отпечатка (browser)."""
        monkeypatch.setattr(cl, "load_settings", lambda: {
            "browser": {"locale": "ru-RU", "timezone": "Europe/Moscow",
                        "geoip": False, "hide_setters": True,
                        "persistent_profile": True, "pinned_fingerprint": True,
                        "profile_dir": "data/camoufox_profile"},
        })
        assert cl.get_browser_config("locale", "en-US") == "ru-RU"
        assert cl.get_browser_config("timezone", "UTC") == "Europe/Moscow"
        assert cl.get_browser_config("geoip", True) is False
        assert cl.get_browser_config("hide_setters", False) is True
        assert cl.get_browser_config("persistent_profile", False) is True
        assert cl.get_browser_config("pinned_fingerprint", False) is True
        assert cl.get_browser_config("profile_dir", "") == "data/camoufox_profile"
        assert cl.get_browser_config("missing", "d") == "d"

    def test_get_antidetect_config_humanize_default(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"antidetect": {}})
        assert cl.get_antidetect_config("humanize", True) is True

    def test_get_learning_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"learning": {"hint_ttl_days": 90}})
        assert cl.get_learning_config("hint_ttl_days", 0) == 90

    def test_get_pdf_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"pdf_parser": {"use_llm": True}})
        assert cl.get_pdf_config("use_llm", False) is True

    def test_get_ductwork_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"ductwork": {"enabled": True, "price_per_m2": 500}})
        assert cl.get_ductwork_config("enabled", False) is True
        assert cl.get_ductwork_config("price_per_m2", None) == 500
        assert cl.get_ductwork_config("missing", "d") == "d"

    def test_get_ductwork_enabled_default_false(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {})
        assert cl.get_ductwork_enabled() is False

    def test_get_ductwork_enabled_true(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"ductwork": {"enabled": True}})
        assert cl.get_ductwork_enabled() is True

    def test_save_ductwork_enabled(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"run": {}})
        cl.save_ductwork_enabled(True)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["ductwork"]["enabled"] is True
        assert cl.get_ductwork_enabled() is True
        cl.save_ductwork_enabled(False)
        assert cl.get_ductwork_enabled() is False

    def test_get_deps_config_empty(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {})
        assert cl.get_deps_config() == {}


class TestSave:
    def test_save_browser_headless(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"browser": {"headless": False}})
        cl.save_browser_headless(True)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["browser"]["headless"] is True
        assert cl.load_settings()["browser"]["headless"] is True

    def test_save_browser_backend(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"browser": {"backend": "camoufox"}})
        cl.save_browser_backend("nodriver")
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["browser"]["backend"] == "nodriver"
        assert cl.load_settings()["browser"]["backend"] == "nodriver"

    def test_save_fresh(self, tmp_path, monkeypatch):
        """Legacy save_fresh пишет инверсию в run.reuse_price."""
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"run": {"fresh": False}})
        cl.save_fresh(True)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["run"]["reuse_price"] is False

    def test_save_run_flags_roundtrip(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"run": {}})
        cl.save_run_flags(reuse_price=False, use_approaches=False, use_site_ranking=True)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["run"]["reuse_price"] is False
        assert written["run"]["use_approaches"] is False
        assert written["run"]["use_site_ranking"] is True

    def test_save_run_flags_partial_preserves_others(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"run": {"use_approaches": True, "reuse_price": True}})
        cl.save_run_flags(use_approaches=False)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["run"]["use_approaches"] is False
        assert written["run"]["reuse_price"] is True

    def test_get_run_flags_defaults_and_legacy_fresh(self, tmp_path, monkeypatch):
        # Нет ключей — дефолты: всё включено.
        _redirect_config(tmp_path, monkeypatch, {"run": {}})
        assert cl.get_run_flags() == {"reuse_price": True, "use_approaches": True, "use_site_ranking": True}

        # Legacy: есть только fresh (без reuse_price) → reuse_price = not fresh.
        _redirect_config(tmp_path, monkeypatch, {"run": {"fresh": True}})
        assert cl.get_run_flags()["reuse_price"] is False
        _redirect_config(tmp_path, monkeypatch, {"run": {"fresh": False}})
        assert cl.get_run_flags()["reuse_price"] is True

        # Новый ключ reuse_price приоритетнее legacy fresh.
        _redirect_config(tmp_path, monkeypatch, {"run": {"fresh": True, "reuse_price": True}})
        assert cl.get_run_flags()["reuse_price"] is True

    def test_save_theme(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"ui": {"theme": "dark"}})
        cl.save_theme("light")
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["ui"]["theme"] == "light"

    def test_save_deps_config(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"deps": {}})
        cl.save_deps_config({"playwright_mcp": {"version": "0.0.79"}})
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["deps"]["playwright_mcp"]["version"] == "0.0.79"

    def test_get_llm_config(self, monkeypatch):
        lm = {"provider": "routerai", "model": "m1"}
        monkeypatch.setattr(cl, "load_settings", lambda: {"llm": lm})
        assert cl.get_llm_config() == lm
        monkeypatch.setattr(cl, "load_settings", lambda: {})
        assert cl.get_llm_config() == {}

    def test_save_llm_settings_roundtrip(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"llm": {"temperature": 0.3}})
        cl.save_llm_settings(
            provider="opencode",
            model="deepseek-v4-flash-free",
            temperature=0.25,
            timeout=180,
            base_urls={"opencode": "https://opencode.ai/zen/v1"},
        )
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["llm"]["provider"] == "opencode"
        assert written["llm"]["model"] == "deepseek-v4-flash-free"
        assert abs(written["llm"]["temperature"] - 0.25) < 1e-9
        assert written["llm"]["timeout"] == 180
        assert written["llm"]["providers"]["opencode"]["base_url"] == "https://opencode.ai/zen/v1"
        assert "api_key" not in written["llm"]

    def test_save_llm_settings_preserves_other_providers(self, tmp_path, monkeypatch):
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        existing = {"llm": {"providers": {
            "lmstudio": {"base_url": "http://localhost:1234/v1"},
            "routerai": {"base_url": "https://routerai.ru/api/v1"},
        }}}
        _redirect_config(tmp_path, monkeypatch, existing)
        cl.save_llm_settings("routerai", "deepseek/deepseek-v4-flash", 0.3, 150,
                             base_urls={"routerai": "https://mirror.routerai.ru/api/v1"})
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        providers = written["llm"]["providers"]
        assert providers["lmstudio"]["base_url"] == "http://localhost:1234/v1"
        assert providers["routerai"]["base_url"] == "https://mirror.routerai.ru/api/v1"
