import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from src.mcp_bridge import _is_hash_ref

logger = logging.getLogger("pricer.memory")


class MemoryManager:
    def __init__(self, engine):
        self._engine = engine

    # ── Approaches ──

    def get_best_approach(self, product_type: str, site: str) -> dict | None:
        return self._engine.get_best_approach(product_type, site)

    def get_all_approaches(self, product_type: str) -> list[dict]:
        return self._engine.get_approaches(product_type)

    def get_site_approaches(self, product_type: str, site: str) -> list[dict]:
        return self._engine.get_approaches(product_type, site)

    def deduplicate_approaches(self, product_type: str, site: str, pattern: list) -> list[dict]:
        existing = self._engine.get_approaches(product_type, site)
        if existing:
            return [max(existing, key=lambda a: a.get("success_count", 0))]
        return []

    def get_approaches_by_site(self, site: str) -> list[dict]:
        return self._engine.get_approaches_by_site(site)

    def get_all_approaches_flat(self) -> list[dict]:
        return self._engine.get_all_approaches()

    # ── Matching equivalences ──

    def record_matching_equivalence(self, spec_text: str, found_name: str) -> None:
        return self._engine.record_matching_equivalence(spec_text, found_name)

    def has_matching_equivalence(self, spec_text: str, found_name: str) -> bool:
        return self._engine.has_matching_equivalence(spec_text, found_name)

    def get_matching_equivalences(self) -> list[dict]:
        return self._engine.get_matching_equivalences()

    @staticmethod
    def clean_steps(steps: list[dict]) -> list[dict]:
        """Clean approach steps for replay: remove hash refs, hallucinated tools, and trim length."""
        if not steps:
            return steps
        cleaned = []
        seen_urls = set()
        for s in steps:
            action = s.get("action", "")
            # Skip screenshot and hallucinated tools
            if action in ("browser_take_screenshot", "browser_find"):
                continue
            # Skip duplicate consecutive snapshots
            if action in ("browser_snapshot", "snapshot") and cleaned and cleaned[-1].get("action") in ("browser_snapshot", "snapshot"):
                continue
            # Remove hash refs — they're ephemeral per session
            target = s.get("target", "")
            if _is_hash_ref(target):
                s = {k: v for k, v in s.items() if k != "target"}
            # Deduplicate navigates to same URL
            if action == "browser_navigate":
                url = s.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            cleaned.append(s)
        # Cap approach length — longer approaches are just failed attempts
        if len(cleaned) > 8:
            cleaned = cleaned[:8]
        return cleaned

    @staticmethod
    def _classify_intent(action: str, target: str, text: str) -> str:
        al = action.lower()
        t = (target or "").lower()
        if "navigate" in al:
            if t.startswith("http"):
                host = urlparse(t).hostname or ""
                if host in ("yandex.ru", "ya.ru", "google.com", "google.ru"):
                    return "open_search_engine"
            elif "yandex" in t or "google" in t:
                return "open_search_engine"
            return "open_site_page"
        if "click" in al:
            if any(x in t for x in ("search", "find", "поиск")):
                return "click_search_button"
            if any(x in t for x in ("cart", "card", "product", "товар", "карточк")):
                return "open_product_card"
            if any(x in t for x in ("catalog", "catalogue", "каталог")):
                return "open_catalog"
            if any(x in t for x in ("submit", "ok", "найти", "применить")):
                return "submit_search_form"
            return "click_element"
        if "type" in al:
            if text and len(text) > 3:
                return "type_search_query"
            return "type_text"
        if "press" in al or "key" in al:
            if "enter" in (text or "").lower():
                return "submit_search"
            if "escape" in (text or "").lower() or "esc" in (text or "").lower():
                return "close_modal"
            return "press_key"
        if "extract" in al or "text" in al:
            return "extract_price_content"
        if "query" in al or "dom" in al:
            if "price" in t:
                return "find_price_element"
            return "find_dom_element"
        if "snapshot" in al:
            return "observe_page"
        if "wait" in al:
            return "wait_for_load"
        return action

    def save_approach(self, product_type: str, site: str,
                      concrete_steps: list,
                      selectors_cache: dict | None = None,
                      param_slots: dict | None = None,
                      method: str = "", search_query: str = "",
                      notes: str = "", success_count: int | None = None) -> int:
        pattern = []
        for step in concrete_steps:
            action = step.get("action", "")
            target = step.get("target") or step.get("element") or ""
            text = step.get("text", "")
            intent = self._classify_intent(action, target, text)
            ps = {"action": action, "intent": intent, "configurable": False}
            if step.get("param_slot"):
                ps["configurable"] = True
                ps["param"] = step["param_slot"]
            pattern.append(ps)

        # Проверяем, есть ли подход с ИДЕНТИЧНЫМ паттерном (та же стратегия).
        # Старая логика: deduplicate_approaches возвращал ЛЮБОЙ подход для пары (pt, site)
        # и перезаписывала его → в graph хранился только 1 подход на пару.
        # Новая логика: перезаписываем ТОЛЬКО при полном совпадении паттерна
        # (те же действия в том же порядке) —allows different strategies to coexist.
        existing = self._find_matching_approach(product_type, site, pattern)
        if existing:
            existing["concrete"] = concrete_steps
            existing["selectors_cache"] = selectors_cache or {}
            existing["param_slots"] = param_slots or {}
            existing["method"] = method
            existing["search_query"] = search_query
            existing["notes"] = notes
            return self._engine.save_approach(existing)

        return self._engine.save_approach({
            "product_type_id": product_type,
            "site_id": site,
            "pattern": pattern,
            "concrete": concrete_steps,
            "selectors_cache": selectors_cache or {},
            "param_slots": param_slots or {},
            "method": method,
            "search_query": search_query,
            "notes": notes,
            **({"success_count": success_count} if success_count is not None else {}),
        })

    def _find_matching_approach(self, product_type: str, site: str,
                                 pattern: list) -> dict | None:
        """Найти существующий подход с идентичным паттерном (для обновления).
        Возвращает None, если подхода с таким паттерном нет — тогда создаётся новый."""
        existing = self._engine.get_approaches(product_type, site)
        if not existing:
            return None
        for appr in existing:
            old_pattern = appr.get("pattern", [])
            if len(old_pattern) != len(pattern):
                continue
            match = all(
                old_p.get("action") == p.get("action")
                and old_p.get("intent") == p.get("intent")
                for old_p, p in zip(old_pattern, pattern)
            )
            if match:
                return appr
        return None

    def record_success(self, approach_id: int):
        self._engine.update_approach_success(approach_id)

    def record_failure(self, approach_id: int):
        self._engine.update_approach_failure(approach_id)

    def increment_consecutive_failures(self, product_type: str, site_id: str):
        try:
            with self._engine._lock:
                self._engine._conn.execute(
                    "UPDATE product_sites SET consecutive_failures = consecutive_failures + 1 "
                    "WHERE product_type_id = ? AND site_id = ?",
                    (product_type, site_id)
                )
                self._engine._conn.commit()
        except Exception as e:
            logger.warning("Failed to increment consecutive_failures for %s/%s: %s", product_type, site_id, e)

    def record_soldat(self, product_type: str, site: str):
        if product_type == "unknown" or not site:
            return
        # Skip search engines — they are not suppliers
        search_engines = {"yandex.ru", "ya.ru", "google.com", "google.ru", "market.yandex.ru"}
        domain = site.replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")
        if domain in search_engines:
            return
        try:
            conn = self._engine._conn
            conn.execute(
                "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'auto')",
                (product_type, f"auto: SOLD_AT {site}")
            )
            conn.execute(
                "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'auto')",
                (site, f"auto: child {product_type}")
            )
            conn.execute(
                "INSERT OR REPLACE INTO concept_edges (child_name, parent_name, relation, weight) VALUES (?, ?, 'SOLD_AT', 1.0)",
                (product_type, site)
            )
            conn.commit()
        except Exception as e:
            logger.warning("Failed to record SOLD_AT concept for %s / %s: %s", product_type, site, e)

    # ── Confirmed prices ──

    def get_relevant_prices(self, spec_text: str, max_results: int = 5,
                            strict_sizes: bool = True,
                            ignore_sizes: bool = False) -> list[dict]:
        return self._engine.get_confirmed_prices(spec_text, max_results,
                                                 strict_sizes=strict_sizes,
                                                 ignore_sizes=ignore_sizes)

    def deduplicate_prices(self, spec_text: str, site: str) -> list[dict]:
        existing = self._engine.get_confirmed_prices(spec_text, 20)
        return [p for p in existing if p.get("site_id") == site]

    def save_price(self, spec_text: str, product_type: str, site: str,
                   price: float, url: str, confidence: float,
                   reason: str = "", source: str = "agent") -> int:
        if confidence < 0.3:
            return 0
        dupes = self.deduplicate_prices(spec_text, site)
        if dupes:
            existing = dupes[0]
            existing["price"] = price
            existing["confidence"] = min(confidence, 1.0)
            existing["url"] = url
            existing["reason"] = reason
            existing["source"] = source
            return self._engine.save_confirmed_price(existing)
        return self._engine.save_confirmed_price({
            "spec_text": spec_text,
            "product_type_id": product_type,
            "site_id": site,
            "price": price,
            "currency": "RUB",
            "url": url,
            "confidence": min(confidence, 1.0),
            "source": source,
            "reason": reason,
        })

    # ── Hints ──

    def get_hints(self, product_type: str) -> list[dict]:
        return self._engine.get_hints(product_type)

    def add_hint(self, product_type: str, text: str,
                 site: str | None = None, priority: float = 0.5) -> int:
        return self._engine.save_hint(product_type, site, text, priority)

    # ── Sites ──

    def get_sites(self, product_type: str) -> list[dict]:
        return self._engine.get_sites_for_product(product_type)

    def add_site(self, domain: str, name: str, product_type: str):
        return self._engine.save_discovered_site(domain, name, product_type)

    # ── Extended CRUD for assistant ──

    def get_all_products(self) -> dict[str, dict]:
        return self._engine.get_all_products()

    def get_all_sites(self) -> dict[str, dict]:
        return self._engine.get_all_sites()

    def get_all_confirmed_prices(self) -> list[dict]:
        return self._engine.get_all_confirmed_prices()

    def get_all_hints(self, product_type: str | None = None) -> list[dict]:
        return self._engine.get_all_hints(product_type)

    def delete_confirmed_price(self, price_id: int) -> bool:
        return self._engine.delete_confirmed_price(price_id)

    def update_confirmed_price(self, price_id: int, spec_text: str, price: float,
                                site: str, confidence: float, reason: str) -> bool:
        return self._engine.update_confirmed_price(price_id, spec_text, price, site, confidence, reason)

    def delete_hint(self, hint_id: int) -> bool:
        return self._engine.delete_hint(hint_id)

    def delete_approach(self, approach_id: int) -> bool:
        return self._engine.delete_approach(approach_id)

    def deprecate_approach(self, approach_id: int) -> bool:
        return self._engine.deprecate_approach(approach_id)

    def delete_product_type(self, product_id: str) -> bool:
        return self._engine.delete_product_type(product_id)

    def save_product_type(self, product_id: str, name: str, category: str = "",
                          keywords: str = "", source: str = "user") -> str:
        return self._engine.save_product_type(product_id, name, category, keywords, source=source)

    def update_product_type_name(self, product_id: str, name: str) -> bool:
        return self._engine.update_product_type_name(product_id, name)

    # ── Categories (группы товаров) ──

    def list_categories(self) -> list[dict]:
        return self._engine.list_categories()

    def save_category(self, category_id: str, name: str, priority: int = 0,
                      focus: str = "", source: str = "user") -> str:
        return self._engine.save_category(category_id, name, priority, focus, source=source)

    def rename_category(self, category_id: str, name: str) -> bool:
        return self._engine.rename_category(category_id, name)

    def delete_category(self, category_id: str) -> tuple[bool, str]:
        return self._engine.delete_category(category_id)

    def set_product_type_category(self, product_id: str, category_id: str) -> bool:
        return self._engine.set_product_type_category(product_id, category_id)

    def split_product_type(self, source_id: str, new_id: str, name: str, category: str,
                           keywords: str, copy_sites: bool = True) -> dict:
        return self._engine.split_product_type(source_id, new_id, name, category,
                                               keywords, copy_sites=copy_sites)

    def preview_split(self, source_id: str, keywords: str) -> dict:
        return self._engine.preview_split(source_id, keywords)

    def save_categories_snapshot(self) -> dict:
        return self._engine.save_categories_snapshot()

    def restore_categories_snapshot(self, snapshot: dict) -> None:
        self._engine.restore_categories_snapshot(snapshot)

    def get_product_sites(self, product_type_id: str) -> list[dict]:
        return self._engine.get_sites_for_product(product_type_id)

    def set_product_site_priority(self, product_type_id: str, site_id: str, priority: int) -> bool:
        return self._engine.set_product_site_priority(product_type_id, site_id, priority)

    def save_concept_edge(self, child: str, parent: str, relation: str = "SOLD_AT", weight: float = 1.0):
        engine = self._engine
        try:
            with engine._lock:
                engine._conn.execute(
                    "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'study')",
                    (child, f"study relation: {relation} {parent}")
                )
                engine._conn.execute(
                    "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'study')",
                    (parent, f"study relation: child {child}")
                )
                engine._conn.execute(
                    "INSERT OR REPLACE INTO concept_edges (child_name, parent_name, relation, weight) VALUES (?, ?, ?, ?)",
                    (child, parent, relation, weight)
                )
                engine._conn.commit()
        except Exception as e:
            logger.warning("Failed to save concept edge %s %s %s: %s", child, relation, parent, e)

    def delete_product_site(self, product_type_id: str, site_id: str) -> bool:
        return self._engine.delete_product_site(product_type_id, site_id)

    # ── Row purge / type overrides (полный контроль строки) ──

    def purge_row(self, spec_text: str, url: str = "", site_id: str = "") -> dict:
        """Полная очистка памяти по строке результата.

        Удаляет/деприкейтит подтверждённые цены, подходы и хинты, связанные с
        этой спецификацией/карточкой. Возвращает счётчики {prices, approaches, hints}.
        """
        prices = self._engine.purge_confirmed_prices(spec_text, url, site_id)
        approaches = self._engine.purge_approaches_for_spec(spec_text, url)
        hints = self._engine.purge_hints_for_spec(spec_text, url)
        return {"prices": prices, "approaches": approaches, "hints": hints}

    def classify_product_type(self, spec_text: str) -> str:
        return self._engine.classify_product_type(spec_text)

    def set_product_type_override(self, spec_text: str, product_type_id: str) -> bool:
        return self._engine.set_product_type_override(spec_text, product_type_id)

    def list_product_type_overrides(self) -> list[dict]:
        return self._engine.list_product_type_overrides()

    def delete_product_type_override(self, spec_text: str) -> bool:
        return self._engine.delete_product_type_override(spec_text)


class ApproachVersioning:
    """Управление версиями и эффективностью подходов (Фаза 4).

    Надстройка над существующими update_approach_success/update_approach_failure.
    success_rate вычисляется на лету из success_count/(success_count+failures_count) —
    отдельной колонки в БД нет.
    """

    def __init__(self, engine, memory_manager):
        self._engine = engine
        self._mm = memory_manager

    def update_effectiveness(self, approach_id: int, success: bool):
        """Обновляет эффективность подхода после использования."""
        if success:
            self._mm.record_success(approach_id)
        else:
            self._mm.record_failure(approach_id)

    def get_effective_approaches(self, site_id: str, limit: int = 5) -> list[dict]:
        """Возвращает наиболее эффективные подходы для сайта, отсортированные по score.

        score = success_rate * 0.7 + freshness * 0.3 (депрекейтнутые ×0.5).
        В каждый возвращаемый подход добавляется поле success_rate.
        """
        approaches = self._mm.get_approaches_by_site(site_id)

        def _score(a: dict) -> float:
            ok = a.get("success_count", 0)
            fail = a.get("failures_count", 0)
            base = ok / max(ok + fail, 1)
            if a.get("is_deprecated"):
                base *= 0.5
            freshness = 0.5
            if a.get("last_success_date"):
                try:
                    days = (datetime.now() - datetime.fromisoformat(a["last_success_date"])).days
                    freshness = max(0.1, 1.0 - days / 30.0)
                except (ValueError, TypeError):
                    freshness = 0.5
            return base * 0.7 + freshness * 0.3

        active = [a for a in approaches if not a.get("is_deprecated")]
        active.sort(key=_score, reverse=True)
        ranked = active[:limit]
        for a in ranked:
            ok = a.get("success_count", 0)
            fail = a.get("failures_count", 0)
            a["success_rate"] = ok / max(ok + fail, 1)
        return ranked


class HintManager:
    """Управление хинтами с TTL (Фаза 4).

    Надстройка над существующими save_hint/get_hints. TTL хранится в колонке
    expires_at (добавлена миграцией в graph_engine._init_db).
    """

    DEFAULT_TTL_DAYS = 90

    def __init__(self, engine, ttl_days: int = DEFAULT_TTL_DAYS):
        self._engine = engine
        self.ttl_days = ttl_days

    def create_hint(self, product_type: str, site: str | None, text: str,
                    priority: float = 0.5, ttl_days: int | None = None) -> int:
        """Создаёт хинт с датой истечения expires_at = now + ttl_days."""
        ttl = ttl_days or self.ttl_days
        expires_at = (datetime.now() + timedelta(days=ttl)).isoformat()
        return self._engine.save_hint(product_type, site, text, priority, expires_at=expires_at)

    def get_active_hints(self, product_type: str, site: str | None = None) -> list[dict]:
        """Возвращает непросроченные хинты (опционально фильтр по site)."""
        hints = self._engine.get_hints(product_type)
        now = datetime.now()
        result = []
        for h in hints:
            exp = h.get("expires_at")
            if exp and datetime.fromisoformat(exp) <= now:
                continue
            if site is not None and h.get("site_id") not in (None, "", site):
                continue
            result.append(h)
        return result

    def cleanup_expired(self) -> int:
        """Удаляет просроченные хинты, возвращает число удалённых."""
        return self._engine.delete_expired_hints()
