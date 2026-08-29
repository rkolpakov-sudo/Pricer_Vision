"""Менеджер сессий: сохранение/восстановление состояния обработки.

Qt-free модуль — тестируется без QApplication.

Позволяет сохранять полное состояние сессии (результаты, кэши, флаги)
и восстанавливать его после перезапуска приложения.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("pricer.session")

SESSION_VERSION = 1
SESSIONS_DIR = "data/sessions"
AUTO_SAVE_NAME = "_current.json"


def _sessions_dir() -> Path:
    """Каталог сессий (создаётся при первом обращении)."""
    base = Path(os.path.dirname(os.path.abspath(__file__))).parent
    d = base / SESSIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def auto_save_path() -> str:
    """Путь к файлу автосохранения."""
    return str(_sessions_dir() / AUTO_SAVE_NAME)


def save_session(path: str, state: dict) -> None:
    """Сериализует состояние сессии в JSON.

    state должен содержать:
        spec_path, total_rows, results, run_flags,
        а также опционально: negative_cache, site_blacklist,
        session_facts, skip_registry, metrics, log_entries
    """
    data = {
        "version": SESSION_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "spec_path": state.get("spec_path", ""),
        "spec_name": Path(state.get("spec_path", "")).stem,
        "total_rows": state.get("total_rows", 0),
        "processed_count": len(state.get("results", [])),
        "found_count": sum(
            1 for r in state.get("results", []) if r.get("price") is not None
        ),
        "results": state.get("results", []),
        "run_flags": state.get("run_flags", {}),
        "negative_cache": state.get("negative_cache", {}),
        "site_blacklist": state.get("site_blacklist", {}),
        "session_facts": state.get("session_facts", {}),
        "skip_registry": state.get("skip_registry", {}),
        "metrics": state.get("metrics", {}),
        "log_entries": state.get("log_entries", []),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    logger.info("Session saved to %s (%d results)", path, data["processed_count"])


def load_session(path: str) -> dict:
    """Десериализует сессию из JSON. Возвращает dict или пустой dict при ошибке."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Failed to load session from %s: %s", path, e)
        return {}
    if data.get("version") != SESSION_VERSION:
        logger.warning("Session version mismatch: %s (expected %s)",
                       data.get("version"), SESSION_VERSION)
    # Восстанавливаем set из списка
    sb = data.get("site_blacklist", {})
    if "successful" in sb and isinstance(sb["successful"], list):
        sb["successful"] = set(sb["successful"])
    sr = data.get("skip_registry", {})
    if "excluded" in sr and isinstance(sr["excluded"], list):
        sr["excluded"] = set(sr["excluded"])
    logger.info("Session loaded from %s (%d results)", path, data.get("processed_count", 0))
    return data


def list_sessions(sessions_dir: str | None = None) -> list[dict]:
    """Возвращает список последних сессий, отсортированных по дате (новые первые).

    Каждая сессия: {path, saved_at, spec_name, total_rows, processed_count, found_count}
    """
    d = Path(sessions_dir) if sessions_dir else _sessions_dir()
    if not d.exists():
        return []
    sessions = []
    for fp in d.glob("*.json"):
        if fp.name == AUTO_SAVE_NAME:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "path": str(fp),
                "saved_at": data.get("saved_at", ""),
                "spec_name": data.get("spec_name", fp.stem),
                "total_rows": data.get("total_rows", 0),
                "processed_count": data.get("processed_count", 0),
                "found_count": data.get("found_count", 0),
            })
        except Exception:
            continue
    sessions.sort(key=lambda s: s.get("saved_at", ""), reverse=True)
    return sessions


def has_current_session() -> bool:
    """True, если есть автосохранённая сессия (_current.json)."""
    p = Path(auto_save_path())
    return p.exists() and p.stat().st_size > 10


def load_current_session() -> dict:
    """Загружает автосохранённую сессию."""
    return load_session(auto_save_path())


def archive_current_session(spec_path: str = "") -> str | None:
    """Переименовывает _current.json в именованный файл сессии.

    Вызывается при сохранении сессии с новым именем или при выходе.
    Возвращает путь к архивному файлу или None, если нечего архивировать.
    """
    current = Path(auto_save_path())
    if not current.exists():
        return None
    spec_stem = Path(spec_path).stem if spec_path else "session"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{spec_stem}_{ts}.json"
    archive_path = _sessions_dir() / archive_name
    current.rename(archive_path)
    logger.info("Archived session to %s", archive_path)
    return str(archive_path)


def delete_session(path: str) -> bool:
    """Удаляет файл сессии."""
    try:
        p = Path(path)
        if p.exists() and p.suffix == ".json":
            p.unlink()
            logger.info("Deleted session %s", path)
            return True
    except Exception as e:
        logger.error("Failed to delete session %s: %s", path, e)
    return False
