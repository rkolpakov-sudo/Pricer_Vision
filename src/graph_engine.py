import json
import re
import logging
import sqlite3
import threading
import yaml
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from src.config_loader import get_price_config

logger = logging.getLogger("pricer.graph")

STALE_DAYS = get_price_config("stale_days", 30)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS product_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    keywords TEXT,
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
    created_at TEXT DEFAULT (datetime('now'))
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
            self._init_db()
            self._load_indexes()
            self._built = True

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
        self._conn.commit()

    def _load_indexes(self):
        self._approaches_index.clear()
        self._approaches_by_product.clear()
        self._approaches_by_site.clear()
        self._approaches_by_id.clear()
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
            cur = self._conn.execute(
                """INSERT INTO approaches
                (product_type_id, site_id, pattern, concrete, selectors_cache,
                param_slots, method, search_query, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["product_type_id"], data["site_id"],
                    json.dumps(data.get("pattern", []), ensure_ascii=False),
                    json.dumps(data.get("concrete", []), ensure_ascii=False),
                    json.dumps(data.get("selectors_cache", {}), ensure_ascii=False),
                    json.dumps(data.get("param_slots", {}), ensure_ascii=False),
                    data.get("method", ""), data.get("search_query", ""),
                    data.get("notes", ""),
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
                "success_count": 1,
                "failures_count": 0,
                "consecutive_failures": 0,
                "cooldown_until": None,
                "is_deprecated": 0,
                "last_success_date": None,
                "last_failure_date": None,
                "notes": data.get("notes", ""),
                "created_at": datetime.now().isoformat(),
            }
            key = (entry["product_type_id"], entry["site_id"])
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

    def get_confirmed_prices(self, spec_text: str, max_results: int = 5) -> list[dict]:
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
            if info["overlap"] >= 2:
                price = dict(info["price"])
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
        return pid

    # ── Hint operations ──

    def get_hints(self, product_type: str) -> list[dict]:
        self.build()
        return self._hints_by_product.get(product_type, [])

    def save_hint(self, product_type: str, site: str | None, text: str,
                  priority: float = 0.5) -> int:
        self.build()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO hints (product_type_id, site_id, hint_text, priority) "
                "VALUES (?, ?, ?, ?)",
                (product_type, site, text, priority)
            )
            self._conn.commit()
            hid = cur.lastrowid
            self._hints_by_product.setdefault(product_type, []).append({
                "id": hid, "product_type_id": product_type,
                "site_id": site, "hint_text": text, "priority": priority,
            })
        return hid

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
                          keywords: str = "") -> str:
        self.build()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO product_types (id, name, category, keywords) "
                "VALUES (?, ?, ?, ?)",
                (product_id, name, category, keywords)
            )
            self._conn.commit()
            self._all_products[product_id] = {
                "id": product_id, "name": name,
                "category": category, "keywords": keywords,
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

    def classify_product_type(self, spec_text: str) -> str:
        self.build()
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

    # ── YAML seed loading ──

    def load_yaml_seed(self, yaml_path: str):
        self.build()
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        excluded = set(config.get("excluded_sites", []))
        category_map = config.get("category_map", {})

        with self._lock:
            for cat_name, cat_data in category_map.items():
                subs = cat_data.get("subcategories", {})
                if not subs:
                    product_id = cat_name
                    product_name = cat_data.get("focus", cat_name)
                    keywords_list = cat_data.get("keywords", [])
                    keywords_str = ", ".join(keywords_list) if keywords_list else product_name
                    self._conn.execute(
                        "INSERT OR REPLACE INTO product_types (id, name, category, keywords) "
                        "VALUES (?, ?, ?, ?)",
                        (product_id, product_name, cat_name, keywords_str)
                    )
                    self._register_sites(product_id, cat_data.get("sites", []), excluded)
                    continue
                for subcat_key, subcat_data in subs.items():
                    product_id = f"{cat_name}_{subcat_key}"
                    product_name = subcat_data.get("name") or subcat_data.get("focus", subcat_key)
                    keywords_list = subcat_data.get("keywords", [])
                    keywords_str = ", ".join(keywords_list) if keywords_list else product_name

                    self._conn.execute(
                        "INSERT OR REPLACE INTO product_types (id, name, category, keywords) "
                        "VALUES (?, ?, ?, ?)",
                        (product_id, product_name, cat_name, keywords_str)
                    )
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
                "INSERT OR REPLACE INTO product_sites (product_type_id, site_id, priority) "
                "VALUES (?, ?, ?)",
                (product_type_id, site_id, priority)
            )
            self._conn.commit()
            for s in self._product_sites.get(product_type_id, []):
                if s["id"] == site_id:
                    s["priority"] = priority
                    break
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
