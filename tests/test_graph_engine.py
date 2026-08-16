import pytest
from src.graph_engine import GraphEngine


class TestGraphEngine:
    def test_build_creates_tables(self, graph_engine):
        row = graph_engine._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in row]
        for t in ("approaches", "confirmed_prices", "hints", "product_sites", "product_types", "sites"):
            assert t in names

    def test_empty_stats(self, graph_engine):
        stats = graph_engine.get_stats()
        assert stats["approaches"] == 0
        assert stats["confirmed_prices"] == 0
        assert stats["sites"] == 0

    def test_save_and_load_approach(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        assert aid > 0

        approaches = graph_engine.get_approaches("cables", "tinko.ru")
        assert len(approaches) == 1
        assert approaches[0]["id"] == aid
        assert approaches[0]["site_id"] == "tinko.ru"

    def test_get_best_approach(self, graph_engine, sample_approach):
        graph_engine.save_approach(sample_approach)
        best = graph_engine.get_best_approach("cables", "tinko.ru")
        assert best is not None
        assert best["site_id"] == "tinko.ru"

    def test_get_best_approach_empty(self, graph_engine):
        assert graph_engine.get_best_approach("nonexistent", "") is None

    def test_approaches_by_product(self, graph_engine, sample_approach):
        graph_engine.save_approach(sample_approach)
        all_cables = graph_engine.get_approaches("cables")
        assert len(all_cables) == 1

    def test_save_confirmed_price(self, graph_engine):
        pid = graph_engine.save_confirmed_price({
            "spec_text": "test cable",
            "product_type_id": "cables",
            "site_id": "tinko.ru",
            "price": 1500.50,
            "url": "https://tinko.ru/cable1",
        })
        assert pid > 0

        prices = graph_engine.get_confirmed_prices("test cable", max_results=5)
        assert len(prices) >= 1
        assert prices[0]["price"] == 1500.50

    def test_get_confirmed_prices_by_token_overlap(self, graph_engine):
        graph_engine.save_confirmed_price({
            "spec_text": "ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ",
            "product_type_id": "cables",
            "site_id": "tinko.ru",
            "price": 100,
        })
        similar = graph_engine.get_confirmed_prices("ВВГ-нг 3x2.5 ОГНЕСТОЙКИЙ КАБЕЛЬ")
        assert len(similar) >= 1

    def test_save_hint(self, graph_engine):
        hid = graph_engine.save_hint("cables", "tinko.ru", "Искать в разделе Кабель", 0.8)
        assert hid > 0

        hints = graph_engine.get_hints("cables")
        assert len(hints) == 1
        assert hints[0]["hint_text"] == "Искать в разделе Кабель"

    def test_save_discovered_site(self, graph_engine):
        sid = graph_engine.save_discovered_site("new-site.ru", "New Site", "cables")
        assert sid is not None

        sites = graph_engine.get_sites_for_product("cables")
        assert any(s["id"] == "new-site.ru" for s in sites)

    def test_classify_product_type(self, graph_engine):
        assert graph_engine.classify_product_type("ВВГ-нг") == "unknown"
        graph_engine.save_product_type("cables", "Кабели", keywords="ВВГ, NYM, кабель")
        assert graph_engine.classify_product_type("ВВГ-нг 3x1.5") == "cables"

    def test_update_approach_success(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        graph_engine.update_approach_success(aid)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["success_count"] >= 2

    def test_update_approach_failure(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        graph_engine.update_approach_failure(aid)
        a = graph_engine.get_approaches("cables", "tinko.ru")[0]
        assert a["consecutive_failures"] == 1

    def test_cooldown_after_3_failures(self, graph_engine, sample_approach):
        aid = graph_engine.save_approach(sample_approach)
        for _ in range(3):
            graph_engine.update_approach_failure(aid)
        a = graph_engine.get_best_approach("cables", "tinko.ru")
        assert a is None

    def test_yaml_seed_loading(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)
        stats = graph_engine.get_stats()
        assert stats["sites"] >= 2
        assert stats["product_types"] >= 1

        sites = graph_engine.get_sites_for_product("cables_power_cables")
        assert len(sites) == 2

    def test_get_cached_categories(self, graph_engine, sample_yaml):
        graph_engine.load_yaml_seed(sample_yaml)
        cats = graph_engine.get_cached_categories()
        assert "cables_power_cables" in cats
        assert len(cats["cables_power_cables"]["sites"]) == 2

    def test_get_recent_approaches(self, graph_engine, sample_approach):
        for _ in range(3):
            graph_engine.save_approach(sample_approach)
        recent = graph_engine.get_recent_approaches(2)
        assert len(recent) == 2

    def test_build_idempotent(self, graph_engine):
        graph_engine.build()
        graph_engine.build()
        assert graph_engine._built is True

    def test_price_without_product_type(self, graph_engine):
        pid = graph_engine.save_confirmed_price({
            "spec_text": "generic item",
            "product_type_id": None,
            "site_id": "some-site.ru",
            "price": 500,
        })
        assert pid > 0

    def test_save_hint_with_expires_at(self, graph_engine):
        from datetime import datetime, timedelta
        exp = (datetime.now() + timedelta(days=90)).isoformat()
        hid = graph_engine.save_hint("cables", "tinko.ru", "TTL hint", 0.5, expires_at=exp)
        assert hid > 0
        hints = graph_engine.get_hints("cables")
        assert hints[0]["expires_at"] == exp

    def test_delete_expired_hints(self, graph_engine):
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(days=1)).isoformat()
        future = (datetime.now() + timedelta(days=90)).isoformat()
        graph_engine.save_hint("cables", "tinko.ru", "expired", 0.5, expires_at=past)
        graph_engine.save_hint("cables", "keaz.ru", "active", 0.5, expires_at=future)
        graph_engine.save_hint("cables", "keaz.ru", "no-ttl", 0.5, expires_at=None)
        deleted = graph_engine.delete_expired_hints()
        assert deleted == 1
        hints = graph_engine.get_hints("cables")
        texts = {h["hint_text"] for h in hints}
        assert "expired" not in texts
        assert "active" in texts
        assert "no-ttl" in texts

    def test_apply_pragmas(self, graph_engine):
        cache_rows = graph_engine._conn.execute("PRAGMA cache_size").fetchall()
        assert cache_rows and int(cache_rows[0][0]) == -64000
        sync_row = graph_engine._conn.execute("PRAGMA synchronous").fetchone()
        assert int(sync_row[0]) == 1
