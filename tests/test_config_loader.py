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

    def test_get_learning_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"learning": {"hint_ttl_days": 90}})
        assert cl.get_learning_config("hint_ttl_days", 0) == 90

    def test_get_pdf_config(self, monkeypatch):
        monkeypatch.setattr(cl, "load_settings", lambda: {"pdf_parser": {"use_llm": True}})
        assert cl.get_pdf_config("use_llm", False) is True

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
        target = tmp_path / "config" / "settings.yaml"
        target.parent.mkdir(parents=True)
        _redirect_config(tmp_path, monkeypatch, {"run": {"fresh": False}})
        cl.save_fresh(True)
        written = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert written["run"]["fresh"] is True

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
