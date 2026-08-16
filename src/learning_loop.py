import json
import logging
from datetime import datetime
from pathlib import Path

from src.memory_manager import HintManager
from src.config_loader import get_learning_config

logger = logging.getLogger("pricer.learning")

SITE_PROFILES_PATH = "data/site_profiles.json"


class LearningLoop:
    """Замкнутый цикл обучения (Фаза 4).

    Прогон → Анализ результатов → Обновление графа → Улучшение будущих прогонов.
    consolidate_after_run() вызывается из MCPAgentRunner после завершения прогона.
    """

    def __init__(self, graph_engine, memory_manager, hint_manager=None,
                 ttl_days: int | None = None, site_profiles_path: str | None = None):
        self.graph = graph_engine
        self.memory = memory_manager
        self.hints = hint_manager or HintManager(
            graph_engine,
            ttl_days=ttl_days or get_learning_config("hint_ttl_days", HintManager.DEFAULT_TTL_DAYS),
        )
        self.site_profiles_path = site_profiles_path or get_learning_config("site_profiles_path", SITE_PROFILES_PATH)
        self.site_profiles = self._load_site_profiles()
        self.last_run_stats = {}

    def consolidate_after_run(self, run_results: list) -> dict:
        """Анализирует результаты прогона и обновляет знания графа."""
        if not run_results:
            return {"approaches_updated": 0, "new_patterns": 0, "new_hints": 0}
        self._update_approach_effectiveness(run_results)
        new_patterns = self._extract_patterns(run_results)
        new_hints = self._generate_hints(run_results)
        self._update_site_profiles(run_results)
        self._save_run_statistics(run_results)
        self._save_site_profiles()
        return {
            "approaches_updated": len(run_results),
            "new_patterns": len(new_patterns),
            "new_hints": len(new_hints),
        }

    def _update_approach_effectiveness(self, results: list):
        """Обновляет эффективность подходов.

        success/failure уже фиксируются в реальном времени внутри process_row
        (record_success/record_failure), поэтому здесь только агрегация: если в
        результате есть approach_id (в текущем пайплайне отсутствует) — усиливаем
        счётчик; дублирования нет, т.к. record_success в process_row вызывается
        один раз при сохранении.
        """
        for r in results:
            aid = r.get("approach_id")
            if not aid:
                continue
            if r.get("price") is not None:
                self.memory.record_success(aid)
            else:
                self.memory.record_failure(aid)

    def _extract_patterns(self, results: list) -> list:
        """Извлекает паттерны из успешных результатов.

        Текущий результат process_row не содержит selectors/pattern (подход уже
        сохранён в процессе прогона через _save_price_and_approach). Сохраняем
        подход только когда в результате есть реальные селекторы — иначе
        создание "search-only" подходов замусорит граф.
        """
        patterns = []
        for result in results:
            if result.get("price") is None or not result.get("selectors"):
                continue
            product_type = result.get("product_type") or "unknown"
            site = result.get("site") or result.get("site_id") or ""
            if product_type == "unknown" or not site:
                continue
            spec = (result.get("spec_text") or "")[:100]
            aid = self.memory.save_approach(
                product_type=product_type,
                site=site,
                concrete_steps=[{"action": "search", "query": spec}],
                method="browser_search",
                search_query=spec,
                notes=f"auto from run: price {result.get('price')}",
                selectors_cache=result.get("selectors"),
            )
            if aid:
                patterns.append(aid)
        return patterns

    def _generate_hints(self, results: list) -> list:
        """Генерирует TTL-хинты из долгих успешных поисков."""
        new_hints = []
        for result in results:
            elapsed = result.get("elapsed") or 0
            if elapsed <= 60 or result.get("price") is None:
                continue
            site = result.get("site") or result.get("site_id") or ""
            product_type = result.get("product_type") or "unknown"
            if product_type == "unknown" or not site:
                continue
            text = f"Товар '{str(result.get('spec_text', ''))[:60]}' найден после долгого поиска ({elapsed:.0f}s)."
            spec_label = str(result.get("spec_text", ""))[:60]
            existing = self.hints.get_active_hints(product_type, site)
            if any(spec_label in h.get("hint_text", "") for h in existing):
                continue
            try:
                self.hints.create_hint(product_type, site, text, priority=0.3)
                new_hints.append(text)
            except Exception as e:
                logger.warning("Hint generation failed: %s", e)
        return new_hints

    def _update_site_profiles(self, results: list):
        """Обновляет профили сайтов на основе фактических результатов прогона."""
        stats = {}
        for result in results:
            site_id = result.get("site") or result.get("site_id")
            if not site_id:
                continue
            s = stats.setdefault(site_id, {"total": 0, "success": 0, "total_attempts": 0, "blocks": 0})
            s["total"] += 1
            s["total_attempts"] += result.get("elapsed") or 0
            if result.get("price") is not None:
                s["success"] += 1
            if "captcha" in str(result.get("reason", "")).lower():
                s["blocks"] += 1

        for site_id, st in stats.items():
            profile = dict(self.site_profiles.get(site_id, {}))
            profile["success_rate"] = st["success"] / max(st["total"], 1)
            profile["avg_attempts"] = st["total_attempts"] / max(st["total"], 1)
            profile["block_count"] = st["blocks"]
            profile["total_runs"] = profile.get("total_runs", 0) + 1
            profile["last_updated"] = datetime.now().isoformat()
            self.site_profiles[site_id] = profile

    def _save_run_statistics(self, results: list):
        total = len(results)
        found = sum(1 for r in results if r.get("price") is not None)
        self.last_run_stats = {
            "total": total,
            "found": found,
            "success_rate": found / max(total, 1),
            "ts": datetime.now().isoformat(),
        }
        logger.info("Run stats: %s", self.last_run_stats)

    def _load_site_profiles(self) -> dict:
        try:
            path = Path(self.site_profiles_path)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning("Failed to load site profiles from %s: %s", self.site_profiles_path, e)
        return {}

    def _save_site_profiles(self):
        try:
            path = Path(self.site_profiles_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.site_profiles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save site profiles to %s: %s", self.site_profiles_path, e)
