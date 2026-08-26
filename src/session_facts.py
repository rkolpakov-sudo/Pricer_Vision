"""Операционная память текущей строки агента (RowFacts).

Qt-free модуль — тестируется без QApplication.

Проблема: `_trim_messages_for_budget` обрезает историю до ~8000 токенов и
выбрасывает середину диалога. Агент «забывает», что уже извлёк страницу,
повторяет тот же ход (залипания до 15 одинаковых извлечений в прогоне 26.08).

Решение: RowFacts накапливает факты ДЕТЕРМИНИРОВАННО (без LLM) в момент
действий и вставляется свежим user-сообщением перед каждым вызовом LLM.
Обрезка истории перестаёт быть фатальной: факты пересоздаются per-call.

Это НЕ замена решений LLM: блок только констатирует факты («извлечение
повторено N раз», «запросы по домену», «цена-кандидат уже видна»), решение
принимает модель.
"""

import logging

logger = logging.getLogger("pricer.facts")

REPEAT_NOTICE_THRESHOLD = 3
MAX_QUERIES_PER_SITE = 3
MAX_SITES_IN_BLOCK = 6
MAX_ERRORS = 3


class RowFacts:
    """Факты, накопленные за время обработки одной строки спецификации."""

    def __init__(self):
        self._sites: dict[str, dict] = {}
        self._price_candidate_seen = False
        self._card_open = False
        self._recent_errors: list[str] = []

    # --- запись фактов (детерминированно) ---

    def record_site_visit(self, domain: str) -> None:
        if domain:
            self._site(domain)

    def record_query(self, domain: str, query: str) -> None:
        q = (query or "").strip()
        if not domain or not q:
            return
        site = self._site(domain)
        if not site["queries"] or site["queries"][-1] != q:
            site["queries"].append(q[:120])
            site["queries"] = site["queries"][-MAX_QUERIES_PER_SITE:]

    def record_browser_call(self, domain: str, key: str, result_hash: str = "") -> None:
        """Учитывает вызов инструмента. Одинаковый вызов с одинаковым
        результатом подряд увеличивает счётчик повторов (для факта LLM)."""
        if not domain:
            return
        site = self._site(domain)
        site["extractions"] += 1
        call = (key, result_hash)
        if site["last_call"] == call:
            site["repeat_streak"] += 1
        else:
            site["repeat_streak"] = 1
        site["last_call"] = call

    def record_empty_result(self, domain: str) -> None:
        if domain:
            self._site(domain)["status"] = "пустой результат"

    def record_price_candidate(self) -> None:
        self._price_candidate_seen = True

    def record_card_open(self) -> None:
        self._card_open = True

    def record_error(self, message: str) -> None:
        m = (message or "").strip()
        if not m:
            return
        if not self._recent_errors or self._recent_errors[-1] != m:
            self._recent_errors.append(m[:120])
            self._recent_errors = self._recent_errors[-MAX_ERRORS:]

    @property
    def price_candidate_seen(self) -> bool:
        return self._price_candidate_seen

    # --- формирование блока для LLM ---

    def to_prompt_block(self) -> str:
        parts = []
        for domain, site in list(self._sites.items())[:MAX_SITES_IN_BLOCK]:
            line = f"  {domain}: {site['status']}"
            if site["queries"]:
                line += "; запросы: «" + "» / «".join(site["queries"]) + "»"
            if site["repeat_streak"] >= REPEAT_NOTICE_THRESHOLD:
                line += (f"; извлечение страницы повторено {site['repeat_streak']} "
                         "раз подряд с одинаковым результатом")
            parts.append(line)
        if self._price_candidate_seen:
            parts.append("  уже видели цену-кандидата (price_candidate)")
        if self._card_open:
            parts.append("  открыта карточка товара")
        if self._recent_errors:
            errs = " | ".join(self._recent_errors)
            parts.append(f"  последние ошибки: {errs}")
        if not parts:
            return ""
        return "ФАКТЫ СЕССИИ (операционная память, не забывай):\n" + "\n".join(parts)

    def _site(self, domain: str) -> dict:
        site = self._sites.get(domain)
        if site is None:
            site = {"status": "посещён", "queries": [], "extractions": 0,
                    "last_call": None, "repeat_streak": 0}
            self._sites[domain] = site
        return site
