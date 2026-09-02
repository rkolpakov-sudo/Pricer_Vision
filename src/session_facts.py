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
EVALS_WITHOUT_TYPE_NOTICE = 4
SITES_WITHOUT_RESULT_NOTICE = 3


class RowFacts:
    """Факты, накопленные за время обработки одной строки спецификации."""

    def __init__(self):
        self._sites: dict[str, dict] = {}
        self._price_candidate_seen = False
        self._price_candidate_hint: str = ""
        self._card_open = False
        self._recent_errors: list[str] = []
        self._rounds_used: int | None = None
        self._rounds_total: int | None = None
        self._navblocks = 0
        self._visited_urls: list[str] = []
        # --- P1: расширенная память ---
        self._queries_per_site: dict[str, list[dict]] = {}
        self._strategy_phase: str = "exploration"
        self._confirmed_price: dict | None = None
        self._candidate_prices: list[dict] = []
        self._current_site: str = ""
        self._rounds_on_site: int = 0

    # --- запись фактов (детерминированно) ---

    def record_site_visit(self, domain: str) -> None:
        if domain:
            self._site(domain)

    def seen_site(self, domain: str) -> bool:
        """Был ли домен уже посещён в этой строке (для предупреждения о повторном заходе)."""
        return bool(domain and domain in self._sites)

    def record_query(self, domain: str, query: str) -> None:
        q = (query or "").strip()
        if not domain or not q:
            return
        site = self._site(domain)
        if not site["queries"] or site["queries"][-1] != q:
            site["queries"].append(q[:120])
            site["queries"] = site["queries"][-MAX_QUERIES_PER_SITE:]
        # Реальный ввод запроса — сбрасываем счётчик «извлечения без ввода».
        site["evals_since_type"] = 0

    def seen_query(self, domain: str, query: str) -> int:
        """Сколько раз этот ТОЧНЫЙ текст запроса уже вводился на домене."""
        q = (query or "").strip()
        if not domain or not q:
            return 0
        site = self._sites.get(domain)
        if not site:
            return 0
        return site["queries"].count(q)

    def last_query(self, domain: str) -> str:
        """Последний введённый запрос на домене (для сравнения деградации)."""
        site = self._sites.get(domain)
        if not site or not site["queries"]:
            return ""
        return site["queries"][-1]

    def record_browser_call(self, domain: str, key: str, result_hash: str = "") -> None:
        """Учитывает вызов инструмента. Одинаковый вызов с одинаковым
        результатом подряд увеличивает счётчик повторов (для факта LLM)."""
        if not domain:
            return
        site = self._site(domain)
        site["extractions"] += 1
        if key.startswith("evaluate:"):
            site["evals_since_type"] += 1
        call = (key, result_hash)
        if site["last_call"] == call:
            site["repeat_streak"] += 1
        else:
            site["repeat_streak"] = 1
        site["last_call"] = call

    @property
    def evals_without_type(self) -> int:
        """Максимальное число извлечений подряд без ввода запроса (по сайтам)."""
        return max((s.get("evals_since_type", 0) for s in self._sites.values()), default=0)

    def record_empty_result(self, domain: str) -> None:
        if domain:
            self._site(domain)["status"] = "пустой результат"

    def record_price_candidate(self, hint: str = "") -> None:
        self._price_candidate_seen = True
        h = (hint or "").strip()
        if h and len(h) > 120:
            h = h[:120]
        if h:
            self._price_candidate_hint = h

    def record_card_open(self) -> None:
        self._card_open = True

    def record_error(self, message: str) -> None:
        m = (message or "").strip()
        if not m:
            return
        if not self._recent_errors or self._recent_errors[-1] != m:
            self._recent_errors.append(m[:120])
            self._recent_errors = self._recent_errors[-MAX_ERRORS:]

    def record_navblock(self) -> None:
        self._navblocks += 1

    def record_url(self, url: str) -> None:
        u = (url or "").strip().rstrip("/")
        if u and (not self._visited_urls or self._visited_urls[-1] != u):
            self._visited_urls.append(u)
            self._visited_urls = self._visited_urls[-12:]

    def seen_url(self, url: str) -> bool:
        u = (url or "").strip().rstrip("/")
        return bool(u and u in self._visited_urls)

    def set_progress(self, rounds_used: int, rounds_total: int) -> None:
        """Бюджет раундов строки — LLM сам решает, когда сохранить кандидата раньше."""
        self._rounds_used = rounds_used
        self._rounds_total = rounds_total

    # --- P1: расширенная память ---

    def record_query_result(self, domain: str, query: str, result: str, round_num: int) -> None:
        """Записывает результат запроса на сайте (найдено/пусто/ошибка)."""
        if not domain:
            return
        site = self._queries_per_site.setdefault(domain, [])
        site.append({"q": query[:80], "r": result[:40], "rnd": round_num})
        if len(site) > 6:
            self._queries_per_site[domain] = site[-6:]

    def set_strategy_phase(self, phase: str) -> None:
        """Устанавливает текущую фазу поиска."""
        if phase in ("exploration", "yandex_fallback", "save_analog", "finished"):
            self._strategy_phase = phase

    def record_confirmed_price(self, price: float, site: str, confidence: float) -> None:
        """Фиксирует подтверждённую цену."""
        self._confirmed_price = {"price": price, "site": site, "conf": confidence}

    def record_candidate_price(self, price: float, site: str, hint: str = "") -> None:
        """Добавляет цену-кандидата (не подтверждена)."""
        self._candidate_prices.append({"price": price, "site": site, "hint": hint[:60]})
        if len(self._candidate_prices) > 5:
            self._candidate_prices = self._candidate_prices[-5:]

    def set_current_site(self, domain: str, rounds_on_site: int = 0) -> None:
        """Фиксирует текущий сайт и глубину на нём."""
        self._current_site = domain
        self._rounds_on_site = rounds_on_site

    def _recommendation(self) -> str:
        """Детерминированная рекомендация на основе накопленных фактов."""
        n_sites = len(self._sites)
        n_empty = sum(1 for s in self._sites.values() if s["status"] == "пустой результат")
        if self._confirmed_price:
            return "Цена сохранена — можно завершать строку."
        if self._candidate_prices and n_sites >= 3:
            best = max(self._candidate_prices, key=lambda c: c["price"])
            return (f"Лучший кандидат: {best['price']}₽@{best['site']} — "
                    "рассмотри сохранение с пониженным confidence.")
        if n_empty >= 3 and n_sites >= 3:
            return "3+ сайта без цены — попробуй Яндекс или сохрани аналог."
        if self._rounds_on_site >= 8:
            return f"{self._rounds_on_site} шагов на сайте — пора переключиться."
        return ""

    @property
    def price_candidate_seen(self) -> bool:
        return self._price_candidate_seen

    @property
    def price_candidate_hint(self) -> str:
        return self._price_candidate_hint

    @property
    def navblocks(self) -> int:
        return self._navblocks

    def distinct_sites(self) -> int:
        return len(self._sites)

    # --- формирование блока для LLM ---

    def to_prompt_block(self) -> str:
        parts = []
        # Прогресс
        if self._rounds_total is not None and self._rounds_used is not None:
            pct = int(self._rounds_used / self._rounds_total * 100) if self._rounds_total else 0
            parts.append(f"  РАУНД {self._rounds_used}/{self._rounds_total} ({pct}%) "
                         "— если цена найдена — сохраняй сразу, не жди конца")
        # Текущий сайт
        if self._current_site:
            line = f"  сайт: {self._current_site}"
            if self._rounds_on_site:
                line += f" ({self._rounds_on_site} шагов)"
            if self._strategy_phase != "exploration":
                line += f" | фаза: {self._strategy_phase}"
            parts.append(line)
        # Журнал сайтов
        if self._sites:
            parts.append("  ЖУРНАЛ САЙТОВ:")
            for domain, site in list(self._sites.items())[:MAX_SITES_IN_BLOCK]:
                status_icon = "🟢" if site["status"] == "посещён" else "🔴"
                line = f"    {status_icon} {domain}: {site['status']}"
                if site["queries"]:
                    q_strs = [f"«{q}»→{'✓' if site['status'] != 'пустой результат' else 'пусто'}"
                              for q in site["queries"]]
                    line += " | " + ", ".join(q_strs)
                if site["repeat_streak"] >= REPEAT_NOTICE_THRESHOLD:
                    line += f" | ⚠ повтор ×{site['repeat_streak']}"
                if site.get("evals_since_type", 0) >= EVALS_WITHOUT_TYPE_NOTICE:
                    line += (f" | ⚠ запрос не вводился {site['evals_since_type']} "
                             "извлечений — используй browser_type")
                parts.append(line)
        # Кандидаты
        if self._candidate_prices:
            cands = "; ".join(f"{c['price']}₽@{c['site']}" for c in self._candidate_prices[-3:])
            parts.append(f"  КАНДИДАТЫ: {cands} — НЕ СОХРАНЕНЫ")
        if self._price_candidate_seen:
            line = "  цена-кандидат"
            if self._price_candidate_hint:
                line += f": {self._price_candidate_hint}"
            if self._navblocks:
                line += f" | попыток уйти без сохранения: {self._navblocks}"
            parts.append(line)
        # Подтверждённая цена
        if self._confirmed_price:
            cp = self._confirmed_price
            parts.append(f"  ✅ СОХРАНЕНО: {cp['price']}₽@{cp['site']} (conf={cp['conf']:.0%})")
        # Карточка
        if self._card_open:
            parts.append("  открыта карточка товара")
        # Ошибки
        if self._recent_errors:
            errs = " | ".join(self._recent_errors)
            parts.append(f"  последние ошибки: {errs}")
        # Рекомендация
        rec = self._recommendation()
        if rec:
            parts.append(f"  💡 РЕКОМЕНДАЦИЯ: {rec}")
        if not parts:
            return ""
        return "ПАМЯТЬ СТРОКИ (переживает обрезку контекста):\n" + "\n".join(parts)

    def _site(self, domain: str) -> dict:
        site = self._sites.get(domain)
        if site is None:
            site = {"status": "посещён", "queries": [], "extractions": 0,
                    "last_call": None, "repeat_streak": 0, "evals_since_type": 0}
            self._sites[domain] = site
        return site


class SessionFacts:
    """Межстрочные факты прогона: сайт × (тип|бренд) статус и рабочие паттерны.

    Заполняется детерминированно по итогам строк (без LLM):
    - успех строки → has_product + рабочий запрос/URL;
    - force-switch/пустой поиск без кандидата → no_product (НЕ перезаписывает
      has_product — иначе повторим ложный вывод «mircli без радиаторов» 26.08).

    В контекст следующих строк подмешиваются: положительные факты — под флагом
    use_approaches, отрицательные — под use_site_ranking («чистый поиск» = без памяти).
    """

    def __init__(self):
        self._status: dict[tuple[str, str], str] = {}
        self._working: dict[str, dict] = {}

    @staticmethod
    def _key(product_type: str, brand: str) -> str:
        return f"{product_type or 'unknown'}|{brand or ''}".strip("|")

    @staticmethod
    def _norm_site(site: str) -> str:
        s = (site or "").strip().lower().rstrip("/")
        for prefix in ("https://", "http://", "www."):
            if s.startswith(prefix):
                s = s[len(prefix):]
        return s

    def record_success(self, product_type: str, brand: str, site: str,
                       url: str = "", query: str = "") -> None:
        dom = self._norm_site(site)
        if not dom:
            return
        self._status[(self._key(product_type, brand), dom)] = "has_product"
        w = self._working.setdefault(dom, {"queries": [], "urls": []})
        q = (query or "").strip()
        if q and q not in w["queries"]:
            w["queries"].append(q[:120])
            w["queries"] = w["queries"][-2:]
        u = (url or "").strip()
        if u and u not in w["urls"]:
            w["urls"].append(u[:120])
            w["urls"] = w["urls"][-2:]

    def record_no_product(self, product_type: str, brand: str, site: str) -> None:
        dom = self._norm_site(site)
        if not dom:
            return
        key = (self._key(product_type, brand), dom)
        if key in self._status and self._status[key] == "has_product":
            return
        self._status[key] = "no_product"

    def _relevant(self, product_type: str, brand: str) -> list[tuple[str, str]]:
        key = self._key(product_type, brand)
        key_type = f"{product_type}|" if product_type and product_type != "unknown" else ""
        key_brand = f"|{brand}" if brand else ""
        out = []
        for (k, dom), status in self._status.items():
            if k == key or (key_type and k.startswith(key_type)) or (key_brand and k.endswith(key_brand)):
                out.append((dom, status))
        return out

    def to_dict(self) -> dict:
        status = {f"{k[0]}||{k[1]}": v for k, v in self._status.items()}
        return {"status": status, "working": dict(self._working)}

    def from_dict(self, data: dict):
        self._status = {}
        for k, v in data.get("status", {}).items():
            parts = k.split("||", 1)
            if len(parts) == 2:
                self._status[(parts[0], parts[1])] = v
        self._working = dict(data.get("working", {}))

    def to_context_blocks(self, product_type: str, brand: str,
                          limit: int = 4) -> tuple[str, str]:
        """Возвращает (положительный_блок, отрицательный_блок) для контекста."""
        pos, neg = [], []
        for dom, status in self._relevant(product_type, brand)[:limit]:
            if status == "has_product":
                line = f"  {dom}: товар этого типа/бренда есть"
                w = self._working.get(dom)
                if w and w["queries"]:
                    line += "; рабочий запрос: «" + "» / «".join(w["queries"]) + "»"
                if w and w["urls"]:
                    line += "; URL: " + w["urls"][0]
                pos.append(line)
            else:
                neg.append(f"  {dom}: товара этого типа/бренда не найдено")
        return ("\n".join(pos), "\n".join(neg))

    def all_sites_exhausted(self, product_type: str, brand: str,
                            min_sites: int = 3) -> bool:
        """True, если ≥min_sites доменов помечены no_product для данного типа/бренда."""
        relevant = self._relevant(product_type, brand)
        no_product_count = sum(1 for _, status in relevant if status == "no_product")
        return no_product_count >= min_sites
