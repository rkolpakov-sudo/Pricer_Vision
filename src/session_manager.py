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


def _make_json_safe(obj):
    """Рекурсивно конвертирует set → list для JSON-сериализации."""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    return obj


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
        "negative_cache": _make_json_safe(state.get("negative_cache", {})),
        "site_blacklist": _make_json_safe(state.get("site_blacklist", {})),
        "session_facts": _make_json_safe(state.get("session_facts", {})),
        "skip_registry": _make_json_safe(state.get("skip_registry", {})),
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
        # Попытка восстановления из backup-файла
        backup = Path(path).with_name(Path(path).stem + "_backup.json")
        if backup.exists():
            try:
                with open(backup, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("Recovered session from backup %s", backup)
            except Exception:
                logger.error("Backup also corrupt: %s", backup)
                return {}
        else:
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
        # Служебные/бэкап-файлы (_current_backup_*.json и т.п.) — НЕ сессии:
        # иначе диалог «Выбор сессии» показывал дубль текущей сессии.
        if fp.name.startswith("_"):
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
    """True, если есть автосохранённая сессия (_current.json) и она валидна."""
    p = Path(auto_save_path())
    if not p.exists() or p.stat().st_size <= 10:
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        logger.warning("Current session file is corrupt: %s", p)
        return False


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


def recover_corrupted_sessions() -> list[str]:
    """Восстанавливает повреждённые файлы сессий (_corrupted_*.json).

    Обрезает JSON до последнего валидного объекта, либо извлекает массив
    results по скобочному соответствию. Сохраняет как именованную сессию
    и удаляет оригинальный повреждённый файл.
    Возвращает список восстановленных путей.
    """
    d = _sessions_dir()
    recovered = []
    for fp in d.glob("_corrupted_*.json"):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()

            data = None

            # Способ 1: полный JSON
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                pass

            # Способ 2: обрезка до последней закрывающей скобки
            if not data:
                for i in range(len(content) - 1, 0, -1):
                    if content[i] == '}':
                        try:
                            data = json.loads(content[:i + 1])
                            break
                        except (json.JSONDecodeError, ValueError):
                            continue

            # Способ 3: извлечение results по скобочному соответствию
            if not data:
                results = _extract_json_array(content, "results")
                if results is not None:
                    data = {"results": results}

            # Способ 4: извлечение отдельных полей через regex
            if not data:
                data = {}
                for key in ("spec_path", "spec_name", "total_rows", "saved_at"):
                    m = _extract_json_string(content, key)
                    if m is not None:
                        data[key] = m
                results = _extract_json_array(content, "results")
                if results is not None:
                    data["results"] = results

            if not data or not data.get("results"):
                logger.warning("Cannot recover corrupted session %s (no valid data)", fp)
                continue

            # Сохраняем восстановленную сессию
            stem = fp.stem  # _corrupted_20260904_backup
            date_part = stem.replace("_corrupted_", "").replace("_backup", "")
            ts = datetime.now().strftime("%H%M%S")
            archive_name = f"recovered_{date_part}_{ts}.json"
            archive_path = d / archive_name
            data["saved_at"] = data.get("saved_at", datetime.now().isoformat(timespec="seconds"))
            data["spec_name"] = data.get("spec_name", f"Восстановленная ({date_part})")
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            fp.unlink()
            recovered.append(str(archive_path))
            logger.info("Recovered corrupted session -> %s (%d results)",
                        archive_path, len(data.get("results", [])))
        except Exception as e:
            logger.error("Failed to recover %s: %s", fp, e)
    return recovered


def _extract_json_array(content: str, key: str):
    """Извлекает значение ключа-массива из JSON-строки по скобочному соответствию."""
    import re
    pattern = rf'"{key}"\s*:\s*\['
    m = re.search(pattern, content)
    if not m:
        return None
    start = m.end() - 1  # позиция '['
    depth = 0
    for j in range(start, len(content)):
        if content[j] == '[':
            depth += 1
        elif content[j] == ']':
            depth -= 1
        if depth == 0:
            try:
                return json.loads(content[start:j + 1])
            except (json.JSONDecodeError, ValueError):
                return None
    return None


def _extract_json_string(content: str, key: str):
    """Извлекает строковое значение ключа из JSON-строки."""
    import re
    pattern = rf'"{key}"\s*:\s*"([^"]*)"'
    m = re.search(pattern, content)
    return m.group(1) if m else None
