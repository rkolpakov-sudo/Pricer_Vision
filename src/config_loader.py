import logging
import os
import yaml
from pathlib import Path

logger = logging.getLogger("pricer.config")

_SETTINGS_CACHE = None

def load_settings(reload: bool = False) -> dict:
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is not None and not reload:
        return _SETTINGS_CACHE
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load config from %s: %s", path, e)
        cfg = {}
    _SETTINGS_CACHE = cfg
    return cfg

def get_run_config(key: str, default):
    cfg = load_settings()
    return cfg.get("run", {}).get(key, default)

def get_price_config(key: str, default):
    cfg = load_settings()
    return cfg.get("price", {}).get(key, default)

def get_llm_retry_config(key: str, default):
    cfg = load_settings()
    return cfg.get("llm", {}).get("retry", {}).get(key, default)

def get_antidetect_config(key: str, default):
    cfg = load_settings()
    return cfg.get("antidetect", {}).get(key, default)

def get_learning_config(key: str, default):
    cfg = load_settings()
    return cfg.get("learning", {}).get(key, default)

def get_pdf_config(key: str, default):
    cfg = load_settings()
    return cfg.get("pdf_parser", {}).get(key, default)

def save_browser_headless(headless: bool):
    cfg = load_settings()
    cfg.setdefault("browser", {})["headless"] = headless
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def save_fresh(fresh: bool):
    cfg = load_settings()
    cfg.setdefault("run", {})["fresh"] = fresh
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def save_theme(theme: str):
    cfg = load_settings()
    cfg["ui"] = cfg.get("ui", {}) | {"theme": theme}
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def get_deps_config() -> dict:
    cfg = load_settings()
    return cfg.get("deps", {}) or {}

def save_deps_config(deps: dict):
    cfg = load_settings()
    cfg["deps"] = deps
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg
