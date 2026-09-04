import json
import re
import logging
import sqlite3
import threading
import yaml
from datetime import datetime

from src.config_loader import get_price_config
from src.approach_relevance import product_name_matches

logger = logging.getLogger("pricer.graph")

STALE_DAYS = get_price_config("stale_days", 30)

_IS_FAMILY_PAGE_RE = re.compile(r"/catalog/\d+/\d+/i\d+$", re.IGNORECASE)


def _split_keywords(keywords) -> list[str]:
    """Список ключевых слов в нижнем регистре (разделители , ; / пробел)."""
    out = []
    for raw in re.split(r"[,;/]", keywords or ""):
        kw = raw.strip().lower()
        if kw:
            out.append(kw)
    return out


def _tokenize(text: str) -> set[str]:
    """Разбивает текст на токены в нижнем регистре (по разделителям)."""
    return set(re.split(r"[,;/\s\-_.:!?()«»\"'«»]+", (text or "").lower())) - {""}


def _kw_hits(text: str, keywords: list[str]) -> bool:
    """True, если text содержит хотя бы одно ключевое слово ЦЕЛИКОМ (токен-уровень).

    'fan' НЕ совпадёт с 'fantastic', 'пласт' НЕ совпадёт с 'пластик'.
    """
    tokens = _tokenize(text)
    return any(kw in tokens for kw in keywords)


def _is_invalid_price_url(url: str) -> bool:
    """True, если URL — не карточка товара (главная/поисковая/семейная страница).
    Такие цены не должны переиспользоваться как источник."""
    if not url:
        return True
    u = (url or "").split("?")[0].rstrip("/")
    path = u.split("//")[-1]
    domain = path.split("/")[0]
    rest = path[len(domain):].strip("/")
    if not rest:
        return True
    if re.search(r"/search", u, re.IGNORECASE):
        return True
    if _IS_FAMILY_PAGE_RE.search(u.rstrip("/")):
        return True
    return False

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    focus TEXT,
    source TEXT DEFAULT 'yaml',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    keywords TEXT,
    source TEXT DEFAULT 'yaml',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT,
    group_name TEXT,
    source TEXT DEFAULT 'yaml',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_sites (
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT REFERENCES sites(id),
    priority INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    PRIMARY KEY (product_type_id, site_id)
);

CREATE TABLE IF NOT EXISTS approaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT REFERENCES sites(id),
    pattern TEXT NOT NULL,
    concrete TEXT NOT NULL,
    selectors_cache TEXT,
    param_slots TEXT,
    method TEXT,
    search_query TEXT,
    success_count INTEGER DEFAULT 1,
    failures_count INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    cooldown_until TEXT,
    is_deprecated INTEGER DEFAULT 0,
    last_success_date TEXT,
    last_failure_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS confirmed_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text TEXT NOT NULL,
    product_type_id TEXT,
    site_id TEXT,
    price REAL NOT NULL,
    currency TEXT DEFAULT 'RUB',
    url TEXT,
    confidence REAL DEFAULT 0.95,
    source TEXT DEFAULT 'agent',
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_type_id TEXT,
    site_id TEXT,
    hint_text TEXT NOT NULL,
    priority REAL DEFAULT 0.5,
    expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matching_equivalences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text TEXT NOT NULL,
    found_name TEXT NOT NULL,
    source TEXT DEFAULT 'user_confirm',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_matching_equiv ON matching_equivalences(spec_text, found_name);

CREATE TABLE IF NOT EXISTS product_type_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text TEXT NOT NULL,
    product_type_id TEXT NOT NULL,
    source TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (spec_text)
);

CREATE INDEX IF NOT EXISTS idx_approaches_product_site ON approaches(product_type_id, site_id);
CREATE INDEX IF NOT EXISTS idx_approaches_site ON approaches(site_id);
CREATE INDEX IF NOT EXISTS idx_confirmed_spec ON confirmed_prices(spec_text);
CREATE INDEX IF NOT EXISTS idx_confirmed_product ON confirmed_prices(product_type_id);
CREATE INDEX IF NOT EXISTS idx_hints_product ON hints(product_type_id);
CREATE TABLE IF NOT EXISTS concepts (
    name TEXT PRIMARY KEY,
    description TEXT,
    source TEXT DEFAULT 'auto',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS concept_edges (
    child_name TEXT REFERENCES concepts(name),
    parent_name TEXT REFERENCES concepts(name),
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (child_name, parent_name, relation)
);
"""


class GraphEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

        self._approaches_index: dict[tuple[str, str], list[dict]] = {}
        self._approaches_by_product: dict[str, list[dict]] = {}
        self._approaches_by_site: dict[str, list[dict]] = {}
        self._approaches_by_id: dict[int, dict] = {}
        self._product_sites: dict[str, list[dict]] = {}
        self._prices_by_token: dict[str, list[dict]] = {}
        self._hints_by_product: dict[str, list[dict]] = {}
        self._all_sites: dict[str, dict] = {}
        self._all_products: dict[str, dict] = {}
        self._all_categories: dict[str, dict] = {}
        self._type_overrides: dict[str, str] = {}
        self._equivalences: set[tuple[str, str]] = set()
        self._built = False

    def build(self):
        with self._lock:
            if self._built:
                return
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._apply_pragmas()
            self._init_db()
            self._load_indexes()
            self._built = True

    def _apply_pragmas(self):
        """Дополнительные прагмы производительности (WAL/foreign_keys уже в build)."""
        try:
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")   # 64MB кэш
            self._conn.execute("PRAGMA temp_store=MEMORY")
        except Exception as e:
            logger.warning("Failed to apply pragmas: %s", e)

    def _init_db(self):
        for stmt in SCHEMA_SQL.split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s + ";")
        # Migration: add consecutive_failures to product_sites if missing
        try:
            self._conn.execute("ALTER TABLE product_sites ADD COLUMN consecutive_failures INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        # Migration: add expires_at to hints if missing (Phase 4, TTL)
        try:
            self._conn.execute("ALTER TABLE hints ADD COLUMN expires_at TEXT")
        except Exception:
            pass  # column already exists
        # Migration: пометка источника типа ('yaml'/'user') — защита пользовательских
        # правок от перезаписи YAML-сидом при перезагрузке (Этап 5 плана групп).
        try:
            self._conn.execute(
                "ALTER TABLE product_types ADD COLUMN source TEXT DEFAULT 'yaml'"
            )
        except Exception:
            pass  # column already exists
        self._conn.commit()

    def _load_indexes(self):
        self._approaches_index.clear()
        self._approaches_by_product.clear()
        self._approaches_by_site.clear()
        self._approaches_by_id.clear()
        self._type_overrides.clear()
        for row in self._conn.execute(
            "SELECT spec_text, product_type_id FROM product_type_overrides"
        ):
            self._type_overrides[row["spec_text"]] = row["product_type_id"]
        now_iso = datetime.now().isoformat()
        for row in self._conn.execute(
            "SELECT * FROM approaches WHERE is_deprecated = 0 "
            "AND (cooldown_until IS NULL OR cooldown_until < ?)",
            (now_iso,)
        ):
            a = dict(row)
            a["pattern"] = json.loads(a.get("pattern", "[]"))
            a["concrete"] = json.loads(a.get("concrete", "[]"))
            a["selectors_cache"] = json.loads(a.get("selectors_cache", "{}"))
            a["param_slots"] = json.loads(a.get("param_slots", "{}"))
            key = (a["product_type_id"], a["site_id"])
            self._approaches_index.setdefault(key, []).append(a)
            self._approaches_by_product.setdefault(a["product_type_id"], []).append(a)
            self._approaches_by_site.setdefault(a["site_id"], []).append(a)
            self._approaches_by_id[a["id"]] = a

        self._prices_by_token.clear()
        for row in self._conn.execute("SELECT * FROM confirmed_prices ORDER BY confidence DESC"):
            p = dict(row)
            tokens = set(re.findall(r'\w+', p["spec_text"].lower()))
            for token in tokens:
                if len(token) > 2:
                    self._prices_by_token.setdefault(token, []).append(p)

        self._product_sites.clear()
        for row in self._conn.execute(
            "SELECT ps.*, s.name as site_name, s.base_url FROM product_sites ps "
            "JOIN sites s ON s.id = ps.site_id"
        ):
            self._product_sites.setdefault(row["product_type_id"], []).append({
                "id": row["site_id"], "name": row["site_name"],
                "base_url": row["base_url"], "priority": row["priority"],
            })

        self._hints_by_product.clear()
        for row in self._conn.execute("SELECT * FROM hints ORDER BY priority DESC"):
            self._hints_by_product.setdefault(row["product_type_id"], []).append(dict(row))

        self._all_sites.clear()
        for row in self._conn.execute("SELECT * FROM sites"):
            self._all_sites[row["id"]] = dict(row)

        self._all_products.clear()
        for row in self._conn.execute("SELECT * FROM product_types WHERE id != 'unknown'"):
            self._all_products[row["id"]] = dict(row)

        self._all_categories.clear()
        for row in self._conn.execute("SELECT * FROM categories ORDER BY priority ASC, id ASC"):
            self._all_categories[row["id"]] = dict(row)

        self._equivalences.clear()
        for row in self._conn.execute("SELECT spec_text, found_name FROM matching_equivalences"):
            self._equivalences.add((row["spec_text"], row["found_name"]))

        logger.info(f"Graph loaded: {len(self._all_products)} products, "
                    f"{len(self._all_sites)} sites, "
                    f"{sum(len(v) for v in self._approaches_index.values())} approaches, "
                    f"{sum(len(v) for v in self._prices_by_token.values())} price tokens")

    def rebuild(self):
        self._built = False
        self.build()

    @staticmethod
    def _filter_approaches(approaches: list[dict]) -> list[dict]:
        now_iso = datetime.now().isoformat()
        return [
            a for a in approaches
            if not a.get("is_deprecated")
            and (a.get("cooldown_until") is None or a["cooldown_until"] < now_iso)
        ]

    # ── Approach operations ──

    def get_approaches(self, product_type: str, site: str | None = None) -> list[dict]:
        self.build()
        if site:
            return self._filter_approaches(self._approaches_index.get((product_type, site), []))
        return self._filter_approaches(self._approaches_by_product.get(product_type, []))

    def get_approaches_by_site(self, site: str) -> list[dict]:
        self.build()
        return self._filter_approaches(self._approaches_by_site.get(site, []))

    def get_all_approaches(self) -> list[dict]:
        self.build()
        result = []
        for approaches in self._approaches_index.values():
            result.extend(approaches)
        return self._filter_approaches(result)

    def get_all_approaches_for_assistant(self) -> list[dict]:
        self.build()
        result = []
        for approaches in self._approaches_index.values():
            result.extend(approaches)
        return result

    def get_best_approach(self, product_type: str, site: str) -> dict | None:
        approaches = self.get_approaches(product_type, site)
        if not approaches:
            return None

        def sort_key(a):
            freshness = 0.5
            if a.get("last_success_date"):
                try:
                    days = (datetime.now() - datetime.fromisoformat(a["last_success_date"])).days
                    freshness = max(0.1, 1.0 - days / 30.0)
                except (ValueError, TypeError):
                    freshness = 0.5
            return (
                a.get("success_count", 0) * 0.5 +
                freshness * 0.3 -
                a.get("consecutive_failures", 0) * 0.2
            )

        return max(approaches, key=sort_key)

    def save_approach(self, data: dict) -> int:
        self.build()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO product_types (id, name) VALUES (?, ?)",
                (data["product_type_id"], data["product_type_id"])
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO sites (id, name, base_url) VALUES (?, ?, ?)",
                (data["site_id"], data["site_id"], f"https://{data['site_id']}")
            )

            existing_id = data.get("id")

            if existing_id:
                self._conn.execute(
                    """UPDATE approaches SET
                    pattern=?, concrete=?, selectors_cache=?, param_slots=?,
                    method=?, search_query=?, notes=?
                    WHERE id=?""",
                    (
                        json.dumps(data.get("pattern", []), ensure_ascii=False),
                        json.dumps(data.get("concrete", []), ensure_ascii=False),
                        json.dumps(data.get("selectors_cache", {}), ensure_ascii=False),
                        json.dumps(data.get("param_slots", {}), ensure_ascii=False),
                        data.get("method", ""), data.get("search_query", ""),
                        data.get("notes", ""),
                        existing_id,
                    )
                )
                self._conn.commit()
                aid = existing_id
            else:
                cur = self._conn.execute(
                    """INSERT INTO approaches
                    (product_type_id, site_id, pattern, concrete, selectors_cache,
                    param_slots, method, search_query, notes, success_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        data["product_type_id"], data["site_id"],
                        json.dumps(data.get("pattern", []), ensure_ascii=False),
                        json.dumps(data.get("concrete", []), ensure_ascii=False),
                        json.dumps(data.get("selectors_cache", {}), ensure_ascii=False),
                        json.dumps(data.get("param_slots", {}), ensure_ascii=False),
                        data.get("method", ""), data.get("search_query", ""),
                        data.get("notes", ""),
                        # success_count явно передаётся вызывающим кодом:
                        # системное сохранение после цены использует дефолт,
                        # агентская «заглушка» save_approach — 0 (см. agent_loop).
                        int(data.get("success_count", 1) or 0),
                    )
                )
                self._conn.commit()
                aid = cur.lastrowid

            entry = {
                "id": aid,
                "product_type_id": data["product_type_id"],
                "site_id": data["site_id"],
                "pattern": data.get("pattern", []),
                "concrete": data.get("concrete", []),
                "selectors_cache": data.get("selectors_cache", {}),
                "param_slots": data.get("param_slots", {}),
                "method": data.get("method", ""),
                "search_query": data.get("search_query", ""),
                "success_count": int(data.get("success_count", 1) or 0),
                "failures_count": data.get("failures_count", 0),
                "consecutive_failures": data.get("consecutive_failures", 0),
                "cooldown_until": data.get("cooldown_until"),
                "is_deprecated": data.get("is_deprecated", 0),
                "last_success_date": data.get("last_success_date"),
                "last_failure_date": data.get("last_failure_date"),
                "notes": data.get("notes", ""),
                "created_at": data.get("created_at", datetime.now().isoformat()),
            }
            key = (entry["product_type_id"], entry["site_id"])

            if existing_id:
                old = self._approaches_by_id.get(aid)
                if old:
                    self._approaches_by_id[aid] = entry
            else:
                self._approaches_index.setdefault(key, []).append(entry)
                self._approaches_by_product.setdefault(entry["product_type_id"], []).append(entry)
                self._approaches_by_site.setdefault(entry["site_id"], []).append(entry)
                self._approaches_by_id[aid] = entry
        return aid

    def update_approach_success(self, approach_id: int):
        self.build()
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE approaches SET success_count = success_count + 1, "
                "consecutive_failures = 0, last_success_date = ?, "
                "cooldown_until = NULL, is_deprecated = 0 WHERE id = ?",
                (now, approach_id)
            )
            self._conn.commit()
            a = self._approaches_by_id.get(approach_id)
            if a:
                a["success_count"] = a.get("success_count", 0) + 1
                a["consecutive_failures"] = 0
                a["cooldown_until"] = None
                a["is_deprecated"] = 0
                a["last_success_date"] = now

    def update_approach_failure(self, approach_id: int):
        self.build()
        with self._lock:
            row = self._conn.execute(
                "SELECT consecutive_failures FROM approaches WHERE id = ?",
                (approach_id,)
            ).fetchone()
            if not row:
                return
            nf = row["consecutive_failures"] + 1
            now = datetime.now().isoformat()
            cooldown = None
            deprecated = 0
            if nf >= 10:
                deprecated = 1
            elif nf >= 3:
                from datetime import timedelta
                cooldown = (datetime.now() + timedelta(hours=24)).isoformat()
            self._conn.execute(
                "UPDATE approaches SET failures_count = failures_count + 1, "
                "consecutive_failures = ?, cooldown_until = ?, "
                "is_deprecated = ?, last_failure_date = ? WHERE id = ?",
                (nf, cooldown, deprecated, now, approach_id)
            )
            self._conn.commit()
            a = self._approaches_by_id.get(approach_id)
            if a:
                a["failures_count"] = a.get("failures_count", 0) + 1
                a["consecutive_failures"] = nf
                a["cooldown_until"] = cooldown
                a["is_deprecated"] = deprecated
                a["last_failure_date"] = now

    # ── Confirmed price operations ──

    def get_confirmed_prices(self, spec_text: str, max_results: int = 5,
                             strict_sizes: bool = True,
                             ignore_sizes: bool = False) -> list[dict]:
        self.build()
        spec_tokens = {t.lower() for t in re.findall(r'\w+', spec_text) if len(t) > 2}
        if not spec_tokens:
            return []

        candidates = {}
        for token in spec_tokens:
            for price in self._prices_by_token.get(token, []):
                pid = price["id"]
                if pid not in candidates:
                    candidates[pid] = {"price": price, "overlap": 0}
                candidates[pid]["overlap"] += 1

        now = datetime.now()
        scored = []
        for pid, info in candidates.items():
            if info["overlap"] >= 2 and product_name_matches(
                spec_text, info["price"].get("spec_text", ""),
                strict_sizes=strict_sizes, ignore_sizes=ignore_sizes,
            ):
                price = dict(info["price"])
                url = price.get("url") or ""
                # Не возвращаем цены с главной/поисковой/семейной страницы —
                # они не являются карточкой товара и не должны переиспользоваться.
                if _is_invalid_price_url(url):
                    continue
                created_at = price.get("created_at")
                if created_at:
                    try:
                        age_days = (now - datetime.fromisoformat(created_at)).days
                        if age_days > STALE_DAYS:
                            price["confidence"] = price.get("confidence", 0.5) * 0.65
                            price["is_stale"] = True
                    except (ValueError, TypeError):
                        pass
                scored.append(((info["overlap"] / len(spec_tokens)) * price.get("confidence", 0.5), price))

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:max_results]]

    def save_confirmed_price(self, data: dict) -> int:
        self.build()
        with self._lock:
            price_id = data.get("id")
            if price_id:
                self._conn.execute(
                    """UPDATE confirmed_prices
                    SET spec_text=?, product_type_id=?, site_id=?, price=?,
                        currency=?, url=?, confidence=?, source=?, reason=?
                    WHERE id=?""",
                    (
                        data["spec_text"], data.get("product_type_id"),
                        data.get("site_id"), data["price"],
                        data.get("currency", "RUB"), data.get("url", ""),
                        data.get("confidence", 0.95), data.get("source", "agent"),
                        data.get("reason", ""), price_id,
                    )
                )
                self._conn.commit()
                self._built = False
                return int(price_id)

            cur = self._conn.execute(
                """INSERT INTO confirmed_prices
                (spec_text, product_type_id, site_id, price, currency, url, confidence, source, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["spec_text"], data.get("product_type_id"),
                    data.get("site_id"), data["price"],
                    data.get("currency", "RUB"), data.get("url", ""),
                    data.get("confidence", 0.95), data.get("source", "agent"),
                    data.get("reason", ""),
                )
            )
            self._conn.commit()
            pid = cur.lastrowid
            now_iso = datetime.now().isoformat()
            tokens = set(re.findall(r'\w+', data["spec_text"].lower()))
            entry = {
                "id": pid, "spec_text": data["spec_text"],
                "price": data["price"], "site_id": data.get("site_id"),
                "confidence": data.get("confidence", 0.95),
                "url": data.get("url", ""), "created_at": now_iso,
            }
            for token in tokens:
                if len(token) > 2:
                    self._prices_by_token.setdefault(token, []).append(entry)
            self._built = False
        return pid

    # ── Hint operations ──

    def get_hints(self, product_type: str) -> list[dict]:
        self.build()
        return self._hints_by_product.get(product_type, [])

    def save_hint(self, product_type: str, site: str | None, text: str,
                  priority: float = 0.5, expires_at: str | None = None) -> int:
        self.build()
        with self._lock:
            existing = self._conn.execute(
                "SELECT ROWID, priority FROM hints "
                "WHERE product_type_id = ? AND site_id IS ? AND hint_text = ?",
                (product_type, site, text)
            ).fetchone()
            if existing:
                rid, old_pri = existing
                if priority > old_pri:
                    self._conn.execute(
                        "UPDATE hints SET priority = ? WHERE ROWID = ?",
                        (priority, rid)
                    )
                    self._conn.commit()
                return rid
            cur = self._conn.execute(
                "INSERT INTO hints (product_type_id, site_id, hint_text, priority, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (product_type, site, text, priority, expires_at)
            )
            self._conn.commit()
            hid = cur.lastrowid
            self._hints_by_product.setdefault(product_type, []).append({
                "id": hid, "product_type_id": product_type,
                "site_id": site, "hint_text": text, "priority": priority,
                "expires_at": expires_at,
            })
        return hid

    def delete_expired_hints(self) -> int:
        self.build()
        now_iso = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM hints WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now_iso,)
            )
            self._conn.commit()
            deleted = cur.rowcount
        if deleted:
            self._built = False
        return deleted

    # ── Site operations ──

    def get_sites_for_product(self, product_type: str) -> list[dict]:
        self.build()
        return self._product_sites.get(product_type, [])

    def save_discovered_site(self, domain: str, name: str, product_type: str) -> str:
        self.build()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO product_types (id, name) VALUES (?, ?)",
                (product_type, product_type)
            )
            existing = self._conn.execute(
                "SELECT id FROM sites WHERE id = ?", (domain,)
            ).fetchone()
            if not existing:
                self._conn.execute(
                    "INSERT INTO sites (id, name, base_url, source) VALUES (?, ?, ?, 'auto_discovered')",
                    (domain, name, f"https://{domain}")
                )
                self._all_sites[domain] = {
                    "id": domain, "name": name,
                    "base_url": f"https://{domain}", "source": "auto_discovered",
                }
            self._conn.execute(
                "INSERT OR IGNORE INTO product_sites (product_type_id, site_id, priority) "
                "VALUES (?, ?, 0)",
                (product_type, domain)
            )
            self._conn.commit()
            # Add to product_sites index
            if not any(s["id"] == domain for s in self._product_sites.get(product_type, [])):
                self._product_sites.setdefault(product_type, []).append({
                    "id": domain, "name": name,
                    "base_url": f"https://{domain}", "priority": 0,
                })
        return domain

    # ── Product type management ──

    def save_product_type(self, product_id: str, name: str, category: str = "",
                          keywords: str = "", source: str = "user") -> str:
        self.build()
        with self._lock:
            if category:
                self._conn.execute(
                    "INSERT OR IGNORE INTO categories (id, name, source) VALUES (?, ?, 'user')",
                    (category, category)
                )
            self._conn.execute(
                "INSERT OR REPLACE INTO product_types (id, name, category, keywords, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (product_id, name, category, keywords, source)
            )
            self._conn.commit()
            self._all_products[product_id] = {
                "id": product_id, "name": name,
                "category": category, "keywords": keywords, "source": source,
            }
        return product_id

    def delete_product_type(self, product_id: str) -> bool:
        self.build()
        with self._lock:
            self._conn.execute("DELETE FROM product_sites WHERE product_type_id = ?", (product_id,))
            self._conn.execute("DELETE FROM approaches WHERE product_type_id = ?", (product_id,))
            self._conn.execute("DELETE FROM confirmed_prices WHERE product_type_id = ?", (product_id,))
            self._conn.execute("DELETE FROM hints WHERE product_type_id = ?", (product_id,))
            self._conn.execute("DELETE FROM product_types WHERE id = ?", (product_id,))
            self._conn.commit()
        self._built = False
        return True

    # ── Categories (группы товаров) — Этап 1 плана групп ──

    def list_categories(self) -> list[dict]:
        self.build()
        cats = [dict(r) for r in self._conn.execute(
            "SELECT * FROM categories ORDER BY priority ASC, id ASC"
        )]
        # количество типов в категории
        for c in cats:
            try:
                n = self._conn.execute(
                    "SELECT COUNT(*) FROM product_types WHERE category = ? AND id != 'unknown'",
                    (c["id"],)
                ).fetchone()[0]
            except Exception:
                n = 0
            c["type_count"] = n
        return cats

    def save_category(self, category_id: str, name: str, priority: int = 0,
                      focus: str = "", source: str = "user") -> str:
        self.build()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO categories (id, name, priority, focus, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (category_id, name, priority, focus, source)
            )
            self._conn.commit()
        self._built = False
        return category_id

    def rename_category(self, category_id: str, name: str) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "UPDATE categories SET name = ? WHERE id = ?", (name, category_id)
            )
            self._conn.commit()
        self._built = False
        return True

    def delete_category(self, category_id: str) -> tuple[bool, str]:
        """Удалить категорию. Только пустую (без типов) — иначе типы осиротеют."""
        self.build()
        with self._lock:
            cnt = self._conn.execute(
                "SELECT COUNT(*) FROM product_types WHERE category = ? AND id != 'unknown'",
                (category_id,)
            ).fetchone()[0]
            if cnt:
                return False, f"в категории {cnt} тип(ов) — сначала перенеси или удали их"
            self._conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            self._conn.commit()
        self._built = False
        return True, ""

    def save_categories_snapshot(self) -> dict:
        """Снимок категорий + category-колонки product_types для rollback."""
        self.build()
        cats = [dict(r) for r in self._conn.execute("SELECT * FROM categories")]
        types_cat = {}
        for r in self._conn.execute(
            "SELECT id, category FROM product_types WHERE id != 'unknown'"
        ):
            types_cat[r["id"]] = r["category"]
        return {"categories": cats, "product_type_categories": types_cat}

    def restore_categories_snapshot(self, snapshot: dict) -> None:
        """Откат категорий к снимку."""
        if not snapshot:
            return
        with self._lock:
            self._conn.execute("DELETE FROM categories")
            for c in snapshot.get("categories", []):
                self._conn.execute(
                    "INSERT INTO categories (id, name, priority, focus, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (c["id"], c["name"], c.get("priority", 0), c.get("focus", ""),
                     c.get("source", "yaml"), c.get("created_at", ""))
                )
            for pid, cat in snapshot.get("product_type_categories", {}).items():
                self._conn.execute(
                    "UPDATE product_types SET category = ? WHERE id = ?", (cat, pid)
                )
            self._conn.commit()
        self._built = False

    def set_product_type_category(self, product_id: str, category_id: str) -> bool:
        """Перенести тип в другую категорию.

        Меняет ТОЛЬКО product_types.category. Рантайм (classify/сайты/память)
        категорию не читает → перенос безопасен для пайплайна.
        """
        self.build()
        with self._lock:
            if category_id:
                self._conn.execute(
                    "INSERT OR IGNORE INTO categories (id, name, source) VALUES (?, ?, 'user')",
                    (category_id, category_id)
                )
            self._conn.execute(
                "UPDATE product_types SET category = ? WHERE id = ?",
                (category_id, product_id)
            )
            self._conn.commit()
        self._built = False
        return True

    # ── Split типа (Этап 3 плана групп) ──

    def split_product_type(self, source_id: str, new_id: str, name: str, category: str,
                           keywords: str, copy_sites: bool = True) -> dict:
        """Вынести часть товаров из source_id в новый тип new_id.

        - Создаёт тип new_id (source='user') под категорией category.
        - Убирает переданные keywords из source (иначе classify продолжит
          возвращать source для отделяемых товаров).
        - Переносит в new_id confirmed_prices/approaches/hints, чьи spec/search_query/
          hint_text содержат хотя бы один из новых keywords.
        - Копирует стартовый список сайтов source → new_id (SitePage правит дальше).
        """
        self.build()
        new_kws = _split_keywords(keywords)
        if not source_id or source_id == "unknown":
            return {"ok": False, "reason": "укажи исходный тип"}
        if not new_id or not new_kws:
            return {"ok": False, "reason": "укажи id нового типа и хотя бы одно ключевое слово"}
        with self._lock:
            src = self._conn.execute(
                "SELECT * FROM product_types WHERE id = ?", (source_id,)
            ).fetchone()
            if src is None:
                return {"ok": False, "reason": f"исходный тип «{source_id}» не найден"}
            if self._conn.execute(
                "SELECT 1 FROM product_types WHERE id = ?", (new_id,)
            ).fetchone():
                return {"ok": False, "reason": f"тип «{new_id}» уже существует"}

            self._conn.execute(
                "INSERT INTO product_types (id, name, category, keywords, source) "
                "VALUES (?, ?, ?, ?, 'user')",
                (new_id, name, category, keywords)
            )
            # 1) убрать отделяемые keywords из источника
            src_kws = _split_keywords(src["keywords"])
            keep = [k for k in src_kws if k not in new_kws]
            if set(keep) != set(src_kws):
                self._conn.execute(
                    "UPDATE product_types SET keywords = ? WHERE id = ?",
                    (", ".join(keep) if keep else "", source_id)
                )
            # 2) миграция цен/подходов/хинтов по keyword-релевантности
            cp = app = hn = 0
            moved_sites: set[str] = set()
            for row in self._conn.execute(
                "SELECT id, spec_text, site_id FROM confirmed_prices WHERE product_type_id = ?",
                (source_id,)
            ):
                if _kw_hits(row["spec_text"], new_kws):
                    self._conn.execute(
                        "UPDATE confirmed_prices SET product_type_id = ? WHERE id = ?",
                        (new_id, row["id"])
                    )
                    cp += 1
                    if row["site_id"]:
                        moved_sites.add(row["site_id"])
            for row in self._conn.execute(
                "SELECT id, search_query, site_id FROM approaches WHERE product_type_id = ?",
                (source_id,)
            ):
                if _kw_hits(row["search_query"] or "", new_kws):
                    self._conn.execute(
                        "UPDATE approaches SET product_type_id = ? WHERE id = ?",
                        (new_id, row["id"])
                    )
                    app += 1
                    if row["site_id"]:
                        moved_sites.add(row["site_id"])
            for row in self._conn.execute(
                "SELECT id, hint_text, site_id FROM hints WHERE product_type_id = ?",
                (source_id,)
            ):
                if _kw_hits(row["hint_text"] or "", new_kws):
                    self._conn.execute(
                        "UPDATE hints SET product_type_id = ? WHERE id = ?",
                        (new_id, row["id"])
                    )
                    hn += 1
                    if row["site_id"]:
                        moved_sites.add(row["site_id"])
            # 3) стартовый список сайтов НЕ копируется слепо из источника.
            # Фикс: в новый тип попадают только сайты, где у ПЕРЕНЕСЁННЫХ записей
            # (цен/подходов/хинтов) были реальные данные. Иначе insulation
            # унаследовала от tools_general магазины безопасности/электро
            # (tinko/satro-paladin/keaz) и агент уходил не на тот сайт.
            copied_sites = 0
            if copy_sites and moved_sites:
                src_prio = {
                    row["site_id"]: row["priority"]
                    for row in self._conn.execute(
                        "SELECT site_id, priority FROM product_sites WHERE product_type_id = ?",
                        (source_id,)
                    )
                }
                for sid in sorted(moved_sites):
                    self._conn.execute(
                        "INSERT OR IGNORE INTO sites (id, name, base_url) VALUES (?, ?, ?)",
                        (sid, sid, f"https://{sid}")
                    )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO product_sites "
                        "(product_type_id, site_id, priority, consecutive_failures) "
                        "VALUES (?, ?, ?, 0)",
                        (new_id, sid, src_prio.get(sid, 2))
                    )
                    copied_sites += 1
            self._conn.commit()
        self._built = False
        self.build()
        warnings = []
        src_kws_after = _split_keywords(
            self._all_products.get(source_id, {}).get("keywords", ""))
        if not src_kws_after:
            warnings.append("исходный тип остался БЕЗ ключевых слов — classify не будет работать")
        if cp == 0 and app == 0 and hn == 0:
            warnings.append("ни одна запись не перемещена — проверь ключевые слова")
        if copy_sites and copied_sites == 0:
            warnings.append("сайты не скопированы: у перенесённых записей нет сайтов — "
                            "добавь сайты вручную на странице «Сайты»")
        return {
            "ok": True, "new_id": new_id,
            "confirmed_moved": cp, "approaches_moved": app,
            "hints_moved": hn, "sites_copied": copied_sites,
            "warnings": warnings,
        }

    def preview_split(self, source_id: str, keywords: str) -> dict:
        """Dry-run: показывает сколько записей БУДЕТ перемещено при сплите."""
        self.build()
        new_kws = _split_keywords(keywords)
        if not source_id or not new_kws:
            return {"ok": False, "reason": "укажи исходный тип и ключевые слова"}
        with self._lock:
            src = self._conn.execute(
                "SELECT * FROM product_types WHERE id = ?", (source_id,)
            ).fetchone()
            if src is None:
                return {"ok": False, "reason": f"тип «{source_id}» не найден"}
            src_kws = _split_keywords(src["keywords"])
            keep = [k for k in src_kws if k not in new_kws]
            cp = sum(1 for r in self._conn.execute(
                "SELECT spec_text FROM confirmed_prices WHERE product_type_id = ?",
                (source_id,)) if _kw_hits(r["spec_text"], new_kws))
            app = sum(1 for r in self._conn.execute(
                "SELECT search_query FROM approaches WHERE product_type_id = ?",
                (source_id,)) if _kw_hits(r["search_query"] or "", new_kws))
            hn = sum(1 for r in self._conn.execute(
                "SELECT hint_text FROM hints WHERE product_type_id = ?",
                (source_id,)) if _kw_hits(r["hint_text"] or "", new_kws))
            warnings = []
            if not keep:
                warnings.append("ВНИМАНИЕ: все keywords уйдут из источника — classify сломается")
            if cp == 0 and app == 0 and hn == 0:
                warnings.append("ни одна запись не будет перемещена — проверь ключевые слова")
            return {
                "ok": True,
                "confirmed_moved": cp, "approaches_moved": app,
                "hints_moved": hn,
                "src_keywords_remaining": keep,
                "src_keywords_removed": [k for k in src_kws if k not in keep],
                "warnings": warnings,
            }

    def classify_product_type(self, spec_text: str) -> str:
        self.build()
        # Приоритет 1: явный пользовательский override (переклассификация).
        # Точное соответствие по нормализованному spec_text — гарантия, что
        # «КТР-20» не уедет в чужой тип из-за пересечения обозначений.
        override = self._get_type_override(spec_text)
        if override:
            return override

        spec_lower = spec_text.lower()
        best = None
        best_score = 0
        for pid, pdata in self._all_products.items():
            keywords = (pdata.get("keywords") or "").lower()
            if not keywords:
                continue
            score = sum(1 for kw in re.split(r'[,;]\s*', keywords) if kw.strip() in spec_lower)
            if score > best_score:
                best_score = score
                best = pid
        return best or "unknown"

    # ── User type overrides (переклассификация строки) ──

    @staticmethod
    def _normalize_spec_key(spec_text: str) -> str:
        return " ".join((spec_text or "").lower().split())

    def _get_type_override(self, spec_text: str) -> str:
        key = self._normalize_spec_key(spec_text)
        return self._type_overrides.get(key, "")

    def set_product_type_override(self, spec_text: str, product_type_id: str) -> bool:
        """Записывает пользовательскую переклассификацию spec_text → тип."""
        self.build()
        key = self._normalize_spec_key(spec_text)
        if not key or not product_type_id:
            return False
        with self._lock:
            # Тип должен существовать, иначе правило бесполезно.
            if self._conn.execute(
                "SELECT 1 FROM product_types WHERE id = ?", (product_type_id,)
            ).fetchone() is None:
                return False
            self._conn.execute(
                "INSERT INTO product_type_overrides (spec_text, product_type_id, source) "
                "VALUES (?, ?, 'user') "
                "ON CONFLICT(spec_text) DO UPDATE SET product_type_id=excluded.product_type_id",
                (key, product_type_id)
            )
            self._conn.commit()
            self._type_overrides[key] = product_type_id
        return True

    def list_product_type_overrides(self) -> list[dict]:
        self.build()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM product_type_overrides ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_product_type_override(self, spec_text: str) -> bool:
        key = self._normalize_spec_key(spec_text)
        if not key:
            return False
        with self._lock:
            self._conn.execute(
                "DELETE FROM product_type_overrides WHERE spec_text = ?", (key,)
            )
            self._conn.commit()
            self._type_overrides.pop(key, None)
        return True

    # ── YAML seed loading ──

    def load_yaml_seed(self, yaml_path: str):
        self.build()
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        excluded = set(config.get("excluded_sites", []))
        category_map = config.get("category_map", {})

        with self._lock:
            # Категории (группы) из category_map; user-категории не трогаем (OR IGNORE).
            priority_order = config.get("priority_order", []) or list(category_map.keys())
            rank = {cid: i for i, cid in enumerate(priority_order)}
            for cid, cdata in category_map.items():
                cname = cdata.get("focus") or cdata.get("name") or cid
                self._conn.execute(
                    "INSERT OR IGNORE INTO categories (id, name, priority, focus, source) "
                    "VALUES (?, ?, ?, ?, 'yaml')",
                    (cid, cname, rank.get(cid, 0), cdata.get("focus", ""))
                )
            for cat_name, cat_data in category_map.items():
                subs = cat_data.get("subcategories", {})
                if not subs:
                    product_id = cat_name
                    product_name = cat_data.get("focus", cat_name)
                    keywords_list = cat_data.get("keywords", [])
                    keywords_str = ", ".join(keywords_list) if keywords_list else product_name
                    self._upsert_seeded_type(product_id, product_name, cat_name, keywords_str)
                    self._register_sites(product_id, cat_data.get("sites", []), excluded)
                    continue
                for subcat_key, subcat_data in subs.items():
                    product_id = f"{cat_name}_{subcat_key}"
                    product_name = subcat_data.get("name") or subcat_data.get("focus", subcat_key)
                    keywords_list = subcat_data.get("keywords", [])
                    keywords_str = ", ".join(keywords_list) if keywords_list else product_name

                    self._upsert_seeded_type(product_id, product_name, cat_name, keywords_str)
                    self._register_sites(product_id, subcat_data.get("sites", []), excluded)

            hints = config.get("hints", [])
            seen_pids = set()
            for h in hints:
                pid = h.get("product_type_id", "unknown")
                text = h.get("text", "")
                priority = h.get("priority", 0.5)
                if text and pid not in seen_pids:
                    self._conn.execute("DELETE FROM hints WHERE product_type_id = ?", (pid,))
                    seen_pids.add(pid)
                if text:
                    if pid != "unknown":
                        self._conn.execute(
                            "INSERT OR IGNORE INTO product_types (id, name) VALUES (?, ?)",
                            (pid, pid)
                        )
                    self._conn.execute(
                        "INSERT INTO hints (product_type_id, hint_text, priority) VALUES (?, ?, ?)",
                        (pid, text, priority)
                    )

            self._conn.commit()
            self._built = False
        logger.info("YAML seed loaded")

    def _register_sites(self, product_id: str, site_entries: list, excluded: set):
        for entry in site_entries:
            if isinstance(entry, dict):
                site_id = entry.get("site", "")
                p = entry.get("priority", "all")
                priority = {"primary": 0, "secondary": 1}.get(p, 2)
            else:
                site_id = entry
                priority = 2
            if site_id in excluded:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO sites (id, name, base_url, source) "
                "VALUES (?, ?, ?, 'yaml')",
                (site_id, site_id, f"https://{site_id}")
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO product_sites (product_type_id, site_id, priority) "
                "VALUES (?, ?, ?)",
                (product_id, site_id, priority)
            )

    def _upsert_seeded_type(self, product_id: str, name: str, category: str, keywords: str):
        """Вставка типа из YAML-сида.

        Пользовательский тип (source='user') НЕ перезаписывается: если пользователь
        правил name/category/keywords через UI, YAML-перезагрузка их не откатывает
        (Этап 5 плана групп). Регистрация сайтов идёт отдельно (_register_sites).
        """
        existing = self._conn.execute(
            "SELECT source FROM product_types WHERE id = ?", (product_id,)
        ).fetchone()
        if existing and existing["source"] == "user":
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO product_types (id, name, category, keywords, source) "
            "VALUES (?, ?, ?, ?, 'yaml')",
            (product_id, name, category, keywords)
        )

    # ── Stats ──

    def get_stats(self) -> dict:
        self.build()
        with self._lock:
            rows = self._conn.execute(
                "SELECT 'approaches', COUNT(*) FROM approaches UNION ALL "
                "SELECT 'confirmed_prices', COUNT(*) FROM confirmed_prices UNION ALL "
                "SELECT 'hints', COUNT(*) FROM hints UNION ALL "
                "SELECT 'sites', COUNT(*) FROM sites UNION ALL "
                "SELECT 'product_types', COUNT(*) FROM product_types"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_cached_categories(self) -> dict:
        self.build()
        result = {}
        for pid, pdata in self._all_products.items():
            sites = self._product_sites.get(pid, [])
            approaches = self._approaches_by_product.get(pid, [])
            prices = []
            seen_ids = set()
            for token_prices in self._prices_by_token.values():
                for p in token_prices:
                    if p.get("product_type_id") == pid and p.get("id") not in seen_ids:
                        prices.append(p)
                        seen_ids.add(p["id"])
            result[pid] = {
                "name": pdata.get("name", pid),
                "sites": sites,
                "approaches": approaches,
                "prices": prices,
            }
        return result

    def get_recent_approaches(self, limit: int = 5) -> list[dict]:
        self.build()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approaches WHERE is_deprecated = 0 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_products(self) -> dict[str, dict]:
        self.build()
        return dict(self._all_products)

    def get_all_sites(self) -> dict[str, dict]:
        self.build()
        return dict(self._all_sites)

    def get_all_hints(self, product_type: str | None = None) -> list[dict]:
        self.build()
        if product_type:
            return self._hints_by_product.get(product_type, [])
        all_hints = []
        for hints in self._hints_by_product.values():
            all_hints.extend(hints)
        return all_hints

    # ── Matching equivalences (learned from user/LLM confirmations) ──

    @staticmethod
    def _equiv_key(text: str) -> str:
        return " ".join((text or "").lower().split())

    def record_matching_equivalence(self, spec_text: str, found_name: str) -> None:
        """Запоминает подтверждённую пару «спецификация → найденное наименование»."""
        spec = self._equiv_key(spec_text)
        found = self._equiv_key(found_name)
        if not spec or not found:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO matching_equivalences (spec_text, found_name) "
                "VALUES (?, ?)",
                (spec, found),
            )
            self._conn.commit()
            self._equivalences.add((spec, found))

    def has_matching_equivalence(self, spec_text: str, found_name: str) -> bool:
        """True, если пара уже подтверждена пользователем/LLM ранее."""
        return (self._equiv_key(spec_text), self._equiv_key(found_name)) in self._equivalences

    def get_matching_equivalences(self) -> list[dict]:
        self.build()
        with self._lock:
            rows = self._conn.execute(
                "SELECT spec_text, found_name, source, created_at "
                "FROM matching_equivalences ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Memory CRUD (for assistant) ──

    def get_all_confirmed_prices(self) -> list[dict]:
        self.build()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM confirmed_prices ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_confirmed_price(self, price_id: int) -> bool:
        self.build()
        with self._lock:
            self._conn.execute("DELETE FROM confirmed_prices WHERE id = ?", (price_id,))
            self._conn.commit()
        self._built = False
        return True

    def delete_hint(self, hint_id: int) -> bool:
        self.build()
        with self._lock:
            self._conn.execute("DELETE FROM hints WHERE id = ?", (hint_id,))
            self._conn.commit()
        self._built = False
        return True

    def update_confirmed_price(self, price_id: int, spec_text: str, price: float,
                                  site: str, confidence: float, reason: str) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "UPDATE confirmed_prices SET spec_text=?, price=?, site_id=?, "
                "confidence=?, reason=? WHERE id=?",
                (spec_text, price, site, confidence, reason, price_id)
            )
            self._conn.commit()
        self._built = False
        return True

    # ── Additional CRUD for assistant ──

    def delete_approach(self, approach_id: int) -> bool:
        self.build()
        with self._lock:
            self._conn.execute("DELETE FROM approaches WHERE id = ?", (approach_id,))
            self._conn.commit()
        self._built = False
        return True

    def deprecate_approach(self, approach_id: int) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "UPDATE approaches SET is_deprecated = 1 WHERE id = ?", (approach_id,)
            )
            self._conn.commit()
        self._built = False
        return True

    def set_product_site_priority(self, product_type_id: str, site_id: str, priority: int) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO product_types (id, name) VALUES (?, ?)",
                (product_type_id, product_type_id)
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO sites (id, name, base_url, source) VALUES (?, ?, ?, 'manual')",
                (site_id, site_id, f"https://{site_id}")
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO product_sites (product_type_id, site_id, priority) "
                "VALUES (?, ?, ?)",
                (product_type_id, site_id, priority)
            )
            self._conn.commit()
            self._all_sites.setdefault(site_id, {
                "id": site_id, "name": site_id,
                "base_url": f"https://{site_id}", "source": "manual",
            })
            sites = self._product_sites.setdefault(product_type_id, [])
            for s in sites:
                if s["id"] == site_id:
                    s["priority"] = priority
                    break
            else:
                sites.append({
                    "id": site_id, "name": site_id,
                    "base_url": f"https://{site_id}", "priority": priority,
                })
        return True

    def delete_product_site(self, product_type_id: str, site_id: str) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "DELETE FROM product_sites WHERE product_type_id = ? AND site_id = ?",
                (product_type_id, site_id)
            )
            self._conn.commit()
        self._built = False
        return True

    def update_product_type_name(self, product_id: str, name: str) -> bool:
        self.build()
        with self._lock:
            self._conn.execute(
                "UPDATE product_types SET name = ? WHERE id = ?",
                (name, product_id)
            )
            self._conn.commit()
        self._built = False
        return True

    # ── Row purge (полная очистка памяти по строке результата) ──

    def purge_confirmed_prices(self, spec_text: str, url: str = "",
                               site_id: str = "") -> int:
        """Удаляет подтверждённые цены строки: нормализованный spec ИЛИ url.

        Возвращает число удалённых записей. Требует пересборки (build) — лениво.
        """
        self.build()
        rows = self._conn.execute(
            "SELECT id, spec_text, url FROM confirmed_prices"
        ).fetchall()
        spec_norm = self._normalize_spec_key(spec_text)
        with self._lock:
            removed = 0
            for r in rows:
                hit = bool(url) and r["url"] == url
                if not hit and spec_norm and self._normalize_spec_key(r["spec_text"] or "") == spec_norm:
                    hit = True
                if hit:
                    self._conn.execute("DELETE FROM confirmed_prices WHERE id = ?", (r["id"],))
                    removed += 1
            self._conn.commit()
        self._built = False
        return removed

    def purge_approaches_for_spec(self, spec_text: str, url: str = "") -> int:
        """Деприкейтит подходы, обученные на ЭТОЙ строке (search_query == spec
        или concrete содержит url карточки). Удаляет только релевантные — чужие
        подходы сайта не трогаем (иначе сгорят стратегии соседних размеров)."""
        self.build()
        removed = 0
        rows = self._conn.execute(
            "SELECT id, search_query, concrete, notes FROM approaches "
            "WHERE is_deprecated = 0"
        ).fetchall()
        spec_norm = self._normalize_spec_key(spec_text)
        with self._lock:
            for r in rows:
                hit = False
                if spec_norm and self._normalize_spec_key(r["search_query"] or "") == spec_norm:
                    hit = True
                if not hit and url:
                    try:
                        concrete = json.loads(r["concrete"] or "[]")
                        notes = r["notes"] or ""
                        if any(url in str(s.get("url", "")) for s in concrete) or url in notes:
                            hit = True
                    except Exception:
                        hit = False
                if hit:
                    self._conn.execute(
                        "UPDATE approaches SET is_deprecated=1, "
                        "notes=COALESCE(notes,'') || ' | deprecated: user rejected row result' "
                        "WHERE id=?", (r["id"],)
                    )
                    removed += 1
            self._conn.commit()
        self._built = False
        return removed

    def purge_hints_for_spec(self, spec_text: str, url: str = "") -> int:
        """Удаляет хинты, ссылающиеся на удалённую карточку/спецификацию."""
        self.build()
        removed = 0
        rows = self._conn.execute(
            "SELECT id, hint_text FROM hints"
        ).fetchall()
        spec_norm = self._normalize_spec_key(spec_text)
        with self._lock:
            for r in rows:
                text = r["hint_text"] or ""
                if (url and url in text) or (spec_norm and spec_norm in self._normalize_spec_key(text)):
                    self._conn.execute("DELETE FROM hints WHERE id = ?", (r["id"],))
                    removed += 1
            self._conn.commit()
        self._built = False
        return removed

