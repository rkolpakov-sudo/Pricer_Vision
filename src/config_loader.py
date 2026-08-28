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


def get_run_flags() -> dict:
    """Режим поиска (память агента): reuse_price / use_approaches / use_site_ranking.

    reuse_price — legacy-совместимость: если ключ `run.reuse_price` отсутствует,
    используется инверсия старого `run.fresh` (fresh=True = НЕ переиспользовать).
    """
    cfg = load_settings()
    run = cfg.get("run", {}) or {}
    if "reuse_price" in run:
        reuse_price = bool(run["reuse_price"])
    elif "fresh" in run:
        # Legacy: fresh ЯВНО задан → инверсия.
        reuse_price = not bool(run["fresh"])
    else:
        # Ничего не задано — дефолт: переиспользовать цены.
        reuse_price = True
    return {
        "reuse_price": reuse_price,
        "use_approaches": bool(run.get("use_approaches", True)),
        "use_site_ranking": bool(run.get("use_site_ranking", True)),
    }


def _write_run(cfg: dict):
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg


def save_run_flags(reuse_price: bool | None = None,
                   use_approaches: bool | None = None,
                   use_site_ranking: bool | None = None):
    """Сохраняет флажки режима поиска. None = ключ не трогаем."""
    cfg = load_settings()
    run = cfg.setdefault("run", {})
    if reuse_price is not None:
        run["reuse_price"] = bool(reuse_price)
    if use_approaches is not None:
        run["use_approaches"] = bool(use_approaches)
    if use_site_ranking is not None:
        run["use_site_ranking"] = bool(use_site_ranking)
    _write_run(cfg)

def get_price_config(key: str, default):
    cfg = load_settings()
    return cfg.get("price", {}).get(key, default)

def get_llm_retry_config(key: str, default):
    cfg = load_settings()
    return cfg.get("llm", {}).get("retry", {}).get(key, default)


def get_llm_config() -> dict:
    cfg = load_settings()
    return cfg.get("llm", {}) or {}


def save_llm_settings(provider: str, model: str, temperature: float, timeout: int,
                      base_urls: dict | None = None):
    """Сохраняет выбор провайдера/модели. API-ключи НЕ персистятся — они
    каждый запуск парсятся из системы (см. src/llm_providers.py)."""
    cfg = load_settings()
    llm = cfg.setdefault("llm", {})
    llm["provider"] = provider
    llm["model"] = model
    llm["temperature"] = float(temperature)
    llm["timeout"] = int(timeout)
    providers = llm.setdefault("providers", {})
    for pid, base_url in (base_urls or {}).items():
        if not pid or not base_url:
            continue
        entry = providers.get(pid) or {}
        if entry.get("base_url") != base_url:
            entry["base_url"] = base_url
            providers[pid] = entry
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def get_antidetect_config(key: str, default):
    cfg = load_settings()
    return cfg.get("antidetect", {}).get(key, default)

def get_antidetect_site_overrides() -> dict:
    cfg = load_settings()
    return cfg.get("antidetect", {}).get("site_overrides", {}) or {}

def get_mcp_config(key: str, default):
    cfg = load_settings()
    return cfg.get("mcp", {}).get(key, default)

def get_browser_config(key: str, default):
    cfg = load_settings()
    return cfg.get("browser", {}).get(key, default)

def get_learning_config(key: str, default):
    cfg = load_settings()
    return cfg.get("learning", {}).get(key, default)

def get_pdf_config(key: str, default):
    cfg = load_settings()
    return cfg.get("pdf_parser", {}).get(key, default)


def get_ductwork_config(key: str, default):
    cfg = load_settings()
    return cfg.get("ductwork", {}).get(key, default)


def get_ductwork_enabled() -> bool:
    return bool(get_ductwork_config("enabled", False))


def save_ductwork_enabled(enabled: bool):
    cfg = load_settings()
    cfg.setdefault("ductwork", {})["enabled"] = bool(enabled)
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def save_browser_headless(headless: bool):
    cfg = load_settings()
    cfg.setdefault("browser", {})["headless"] = headless
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def save_browser_backend(backend: str):
    cfg = load_settings()
    cfg.setdefault("browser", {})["backend"] = backend
    path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "config" / "settings.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = cfg

def save_fresh(fresh: bool):
    """Legacy: инверсия флажка «Цены из памяти». Пишет в run.reuse_price."""
    save_run_flags(reuse_price=not fresh)

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
