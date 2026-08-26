"""Провайдеры LLM: реестр, динамический резолв креденшиалов, список моделей.

Креденшиалы НЕ хранятся в проекте — при каждом запуске они парсятся из системы:
  1. переменные окружения провайдера (Provider.api_key_envs);
  2. ~/.local/share/opencode/auth.json (ключ сервиса по serviceID);
  3. ~/.hermes/.env (строки KEY=VALUE).
Благодаря этому перенос проекта на другую систему подхватывает ключи новой
системы без правок кода и конфигов. В логи/UI попадает только отпечаток ключа.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger("pricer.llm_providers")

USER_AGENT = "PricerVision/2.0 (price-collection agent)"

CACHE_TTL_SECONDS = 6 * 3600

SOURCE_OVERRIDE = "введён вручную (сессия)"
SOURCE_ENV = "переменная окружения"
SOURCE_OPENCODE = "opencode auth.json"
SOURCE_HERMES = "hermes .env"
SOURCE_NONE = ""


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    api_key_envs: tuple = ()
    default_model: str = ""
    description: str = ""
    requires_key: bool = True
    auth_service_ids: tuple = ()
    extra_headers: tuple = ()  # пары (заголовок, значение), как HTTP-Referer/X-Title у hermes

    @property
    def completions_url(self) -> str:
        return completions_url(self.base_url)


def completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            id="opencode",
            name="OpenCode Zen",
            base_url="https://opencode.ai/zen/v1",
            api_key_envs=("OPENCODE_ZEN_API_KEY", "OPENCODE_API_KEY"),
            auth_service_ids=("opencode",),
            default_model="deepseek-v4-flash-free",
            description="Claude / GPT / Gemini / DeepSeek; pay-as-you-go + бесплатные модели",
            extra_headers=(("HTTP-Referer", "https://opencode.ai"),
                           ("X-Title", "Pricer Vision")),
        ),
        Provider(
            id="routerai",
            name="RouterAI",
            base_url="https://routerai.ru/api/v1",
            api_key_envs=("ROUTERAI_API_KEY",),
            auth_service_ids=("routerai",),
            default_model="deepseek/deepseek-v4-flash",
            description="Российский агрегатор: DeepSeek, Qwen, GPT, Gemini и др. (460+ моделей)",
        ),
        Provider(
            id="lmstudio",
            name="LM Studio (локально)",
            base_url="http://localhost:1234/v1",
            requires_key=False,
            description="Локальный OpenAI-совместимый сервер LM Studio",
        ),
        Provider(
            id="ollama",
            name="Ollama (локально)",
            base_url="http://localhost:11434/v1",
            requires_key=False,
            description="Локальный сервер Ollama",
        ),
        Provider(
            id="llamacpp",
            name="llama.cpp (локально)",
            base_url="http://localhost:8080/v1",
            requires_key=False,
            description="Локальный сервер llama.cpp",
        ),
    )
}


def get_provider(provider_id: str) -> Provider:
    return PROVIDERS.get(provider_id, PROVIDERS["lmstudio"])


# ── Пути к системным конфигам ────────────────────────────────────────────


def opencode_auth_candidates() -> list[Path]:
    home = Path.home()
    roots = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.append(Path(xdg))
    roots.append(home / ".local" / "share")
    return [r / "opencode" / "auth.json" for r in roots]


def hermes_env_candidates() -> list[Path]:
    return [Path.home() / ".hermes" / ".env"]


def opencode_config_candidates() -> list[Path]:
    home = Path.home()
    roots = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        roots.append(Path(xdg))
    roots.append(home / ".config")
    names = ("opencode.jsonc", "opencode.json")
    return [r / "opencode" / n for r in roots for n in names]


# ── Парсеры системных файлов ─────────────────────────────────────────────


def _read_opencode_auth(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for service_id, entry in data.items():
        key = ""
        if isinstance(entry, dict):
            cred = entry.get("credential") or entry
            if isinstance(cred, dict):
                key = cred.get("key") or ""
        elif isinstance(entry, str):
            key = entry
        key = str(key).strip()
        if key:
            out[str(service_id)] = key
    return out


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'").strip()
        if name and value:
            out[name] = value
    return out


# ── Резолв креденшиалов ──────────────────────────────────────────────────

_MANUAL_KEYS: dict[str, str] = {}


def set_manual_key(provider_id: str, key: str):
    """Ручной ключ живёт только в памяти текущего процесса."""
    if key and key.strip():
        _MANUAL_KEYS[provider_id] = key.strip()
    else:
        _MANUAL_KEYS.pop(provider_id, None)


def get_manual_key(provider_id: str) -> str:
    return _MANUAL_KEYS.get(provider_id, "")


def resolve_api_key(provider: "Provider | str", *, env: dict | None = None) -> tuple[str, str]:
    """Возвращает (api_key, источник). Ключ никогда не пишется в лог."""
    prov = get_provider(provider) if isinstance(provider, str) else provider
    env = os.environ if env is None else env

    manual = _MANUAL_KEYS.get(prov.id, "")
    if manual:
        return manual, SOURCE_OVERRIDE

    for var in prov.api_key_envs:
        val = (env.get(var) or "").strip()
        if val:
            return val, SOURCE_ENV

    for path in opencode_auth_candidates():
        if not path.is_file():
            continue
        auth = _read_opencode_auth(path)
        for sid in (prov.auth_service_ids or (prov.id,)):
            val = auth.get(sid, "")
            if val:
                return val, SOURCE_OPENCODE

    hermes_vars = set(prov.api_key_envs)
    for path in hermes_env_candidates():
        if not path.is_file():
            continue
        vals = _parse_env_file(path)
        for var in hermes_vars:
            val = vals.get(var, "")
            if val:
                return val, SOURCE_HERMES

    return "", SOURCE_NONE


def key_fingerprint(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "—"
    if len(key) <= 9:
        return "***"
    return f"{key[:6]}…{key[-3:]}"


# ── Base URL override из opencode.jsonc ──────────────────────────────────

_JSONC_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|/\*.*?\*/|//[^\n]*', re.S)


def strip_jsonc(text: str) -> str:
    def _repl(m: re.Match) -> str:
        token = m.group(0)
        return token if token.startswith('"') else ""
    return _JSONC_TOKEN_RE.sub(_repl, text)


def resolve_base_url_override(provider: "Provider | str") -> str:
    """baseURL из ~/.config/opencode/opencode.json(c), если задан; иначе ''."""
    prov = get_provider(provider) if isinstance(provider, str) else provider
    for path in opencode_config_candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        entry = ((data.get("provider") or {}).get(prov.id)) or {}
        options = entry.get("options") or {}
        url = str(options.get("baseURL") or "").strip()
        if url:
            return url
    return ""


# ── Списки моделей ───────────────────────────────────────────────────────


def parse_models_payload(payload) -> list[dict]:
    """Чистый парсер ответа /models → [{id, name, context_length}]."""
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("models")
    else:
        items = payload
    models: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
            name = str(item.get("name") or "").strip()
            ctx = item.get("context_length")
        else:
            model_id = str(item).strip()
            name = ""
            ctx = None
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({
            "id": model_id,
            "name": name if name and name != model_id else "",
            "context_length": int(ctx) if isinstance(ctx, (int, float)) and ctx else None,
        })
    return models


def fetch_models(base_url: str, api_key: str = "", timeout: float = 8.0) -> list[dict]:
    """GET <base_url>/models с Bearer-авторизацией. Бросает исключение при ошибке."""
    url = (base_url or "").rstrip("/") + "/models"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return parse_models_payload(response.json())


# ── Кэш списков моделей (память + диск) ──────────────────────────────────

_MODELS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_LOCK = threading.Lock()


def _disk_cache_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "llm_providers_cache.json"


def _load_disk_cache() -> dict[str, tuple[float, list[dict]]]:
    try:
        raw = json.loads(_disk_cache_path().read_text(encoding="utf-8"))
        return {
            pid: (float(entry["at"]), list(entry["models"]))
            for pid, entry in raw.items()
            if isinstance(entry, dict) and "at" in entry and "models" in entry
        }
    except Exception:
        return {}


def _store_disk_cache(snapshot: dict[str, tuple[float, list[dict]]]):
    try:
        path = _disk_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {pid: {"at": ts, "models": models} for pid, (ts, models) in snapshot.items()}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("disk cache write failed: %s", e)


def cached_models(provider_id: str, max_age: float = CACHE_TTL_SECONDS) -> list[dict] | None:
    with _CACHE_LOCK:
        entry = _MODELS_CACHE.get(provider_id)
    if entry is None:
        disk = _load_disk_cache()
        with _CACHE_LOCK:
            for pid, item in disk.items():
                _MODELS_CACHE.setdefault(pid, item)
            entry = _MODELS_CACHE.get(provider_id)
    if entry is None:
        return None
    ts, models = entry
    if time.time() - ts > max_age:
        return None
    return [dict(m) for m in models]


def store_models_cache(provider_id: str, models: list[dict]):
    now = time.time()
    with _CACHE_LOCK:
        _MODELS_CACHE[provider_id] = (now, [dict(m) for m in models])
        snapshot = dict(_MODELS_CACHE)
    _store_disk_cache(snapshot)


def get_models_refreshed(base_url: str, provider_id: str, api_key: str = "") -> list[dict]:
    models = fetch_models(base_url, api_key)
    store_models_cache(provider_id, models)
    return models


def get_models_auto(provider: "Provider | str", api_key: str = "", force: bool = False) -> list[dict]:
    """Кэш сессии/диска → живой запрос. Бросает исключение при ошибке сети."""
    prov = get_provider(provider) if isinstance(provider, str) else provider
    if not force:
        cached = cached_models(prov.id)
        if cached is not None:
            return cached
    return get_models_refreshed(prov.base_url, prov.id, api_key)


# ── Фабрика LLMClient ────────────────────────────────────────────────────


def create_llm_client(config: dict, temperature: float | None = None):
    """Единая точка сборки LLMClient из settings.yaml → llm.

    Fallback на локальные URL включается только для локальных провайдеров.
    """
    from src.llm_client import FALLBACK_URLS, LLMClient

    lm = (config or {}).get("llm", {}) or {}
    prov = get_provider(lm.get("provider", "lmstudio"))
    pcfg = (lm.get("providers") or {}).get(prov.id, {}) or {}
    base_url = (pcfg.get("base_url") or "").strip() or resolve_base_url_override(prov) or prov.base_url
    if prov.requires_key:
        api_key, source = resolve_api_key(prov)
        if not api_key:
            logger.warning("LLM %s: API-ключ не найден в системе (env/auth.json/.env)", prov.id)
    else:
        api_key, source = "", ""

    temp = lm.get("temperature", 0.3) if temperature is None else temperature
    headers = {"User-Agent": USER_AGENT}
    headers.update(dict(prov.extra_headers))
    client = LLMClient(
        url=completions_url(base_url),
        model=lm.get("model", ""),
        temperature=float(temp),
        timeout=float(lm.get("timeout", 150)),
        api_key=api_key,
        headers=headers,
    )
    if not prov.requires_key:
        client.set_fallbacks(FALLBACK_URLS)
    logger.info(
        "LLM provider=%s base=%s key_source=%s fingerprint=%s",
        prov.id, base_url, source or "нет", key_fingerprint(api_key),
    )
    return client
