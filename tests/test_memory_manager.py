import pytest
from datetime import datetime, timedelta
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager, HintManager, ApproachVersioning


@pytest.fixture
def mm(graph_engine):
    return MemoryManager(graph_engine)


class TestHintManager:
    def test_create_hint_sets_expiry(self, graph_engine):
        hm = HintManager(graph_engine, ttl_days=90)
        hid = hm.create_hint("cables", "tinko.ru", "Искать в каталоге")
        assert hid > 0
        hints = graph_engine.get_hints("cables")
        assert hints[0]["expires_at"] is not None

    def test_create_hint_custom_ttl(self, graph_engine):
        hm = HintManager(graph_engine, ttl_days=90)
        hm.create_hint("cables", "tinko.ru", "Короткий TTL", ttl_days=1)
        hints = graph_engine.get_hints("cables")
        exp = datetime.fromisoformat(hints[0]["expires_at"])
        assert exp - datetime.now() < timedelta(days=2)

    def test_get_active_hints_excludes_expired(self, graph_engine):
        hm = HintManager(graph_engine)
        hm.create_hint("cables", "tinko.ru", "Активный")
        graph_engine.save_hint("cables", "tinko.ru", "Просроченный",
                               0.5, expires_at=(datetime.now() - timedelta(days=1)).isoformat())
        active = hm.get_active_hints("cables")
        assert [h["hint_text"] for h in active] == ["Активный"]

    def test_get_active_hints_filters_by_site(self, graph_engine):
        hm = HintManager(graph_engine)
        hm.create_hint("cables", "tinko.ru", "Для тинько")
        hm.create_hint("cables", "keaz.ru", "Для кэаз")
        tinko = hm.get_active_hints("cables", "tinko.ru")
        assert len(tinko) == 1
        assert tinko[0]["hint_text"] == "Для тинько"

    def test_cleanup_expired(self, graph_engine):
        hm = HintManager(graph_engine)
        hm.create_hint("cables", "tinko.ru", "Активный")
        graph_engine.save_hint("cables", "keaz.ru", "Старый",
                               0.5, expires_at=(datetime.now() - timedelta(days=1)).isoformat())
        deleted = hm.cleanup_expired()
        assert deleted == 1
        assert len(hm.get_active_hints("cables")) == 1


class TestApproachVersioning:
    def test_update_effectiveness_success(self, graph_engine, mm):
        aid = mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        av = ApproachVersioning(graph_engine, mm)
        av.update_effectiveness(aid, success=True)
        best = mm.get_best_approach("cables", "tinko.ru")
        assert best["success_count"] >= 2

    def test_update_effectiveness_failure(self, graph_engine, mm):
        aid = mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        av = ApproachVersioning(graph_engine, mm)
        av.update_effectiveness(aid, success=False)
        best = mm.get_best_approach("cables", "tinko.ru")
        assert best["consecutive_failures"] == 1

    def test_get_effective_approaches_ranks_by_success_rate(self, graph_engine, mm):
        a1 = mm.save_approach("cables", "tinko.ru", [{"action": "navigate", "url": "a"}])
        a2 = mm.save_approach("cables", "tinko.ru", [{"action": "click", "target": "b"}])
        mm.record_failure(a1)
        mm.record_failure(a1)
        av = ApproachVersioning(graph_engine, mm)
        ranked = av.get_effective_approaches("tinko.ru")
        assert len(ranked) == 2
        assert ranked[0]["id"] == a2  # success_rate=1.0 выше чем 1/3

    def test_get_effective_approaches_adds_success_rate(self, graph_engine, mm):
        mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        av = ApproachVersioning(graph_engine, mm)
        ranked = av.get_effective_approaches("tinko.ru")
        assert ranked[0]["success_rate"] == 1.0

    def test_get_effective_approaches_limit(self, graph_engine, mm):
        for i in range(5):
            mm.save_approach("cables", "tinko.ru", [{"action": "click", "target": f"s{i}"}])
        av = ApproachVersioning(graph_engine, mm)
        assert len(av.get_effective_approaches("tinko.ru", limit=3)) == 3


class TestMemoryManager:
    def test_get_best_approach_empty(self, mm):
        assert mm.get_best_approach("cables", "tinko.ru") is None

    def test_save_and_get_approach(self, mm):
        aid = mm.save_approach(
            product_type="cables", site="tinko.ru",
            concrete_steps=[{"action": "navigate", "url": "https://tinko.ru"}],
        )
        assert aid > 0

        best = mm.get_best_approach("cables", "tinko.ru")
        assert best is not None

    def test_get_all_approaches(self, mm):
        mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        mm.save_approach("cables", "keaz.ru", [{"action": "navigate"}])
        all_a = mm.get_all_approaches("cables")
        assert len(all_a) == 2

    def test_get_site_approaches(self, mm):
        mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        site_a = mm.get_site_approaches("cables", "tinko.ru")
        assert len(site_a) == 1
        assert site_a[0]["site_id"] == "tinko.ru"

    def test_record_success(self, mm):
        aid = mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        mm.record_success(aid)
        best = mm.get_best_approach("cables", "tinko.ru")
        assert best["success_count"] >= 2

    def test_record_failure(self, mm):
        aid = mm.save_approach("cables", "tinko.ru", [{"action": "navigate"}])
        mm.record_failure(aid)
        best = mm.get_best_approach("cables", "tinko.ru")
        assert best["consecutive_failures"] == 1

    def test_save_price_below_threshold(self, mm):
        pid = mm.save_price("test", "cables", "tinko.ru", 100, "http://x.ru", 0.2)
        assert pid == 0

    def test_save_price_rule5_analog_band_accepted(self, mm):
        """Rule 5 аналоги (confidence 0.3-0.5) должны попадать в БД (ранее отбрасывались < 0.6)."""
        pid = mm.save_price("Клапан балансировочный Ду15", "valves", "tinko.ru", 1567, "http://x.ru", 0.4)
        assert pid > 0

    def test_save_price_above_threshold(self, mm):
        pid = mm.save_price("test item", "cables", "tinko.ru", 1500, "http://x.ru", 0.95)
        assert pid > 0

    def test_get_relevant_prices(self, mm):
        mm.save_price("ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ", "cables", "tinko.ru", 100, "http://x.ru", 0.95)
        same = mm.get_relevant_prices("ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ")
        assert len(same) >= 1
        # Разный типоразмер (3x1.5 vs 3x2.5) — цена не переиспользуется
        assert mm.get_relevant_prices("ВВГ-нг 3x2.5 ОГНЕСТОЙКИЙ КАБЕЛЬ") == []

    def test_hints_crud(self, mm):
        hid = mm.add_hint("cables", "Начинать с каталога", priority=0.9)
        assert hid > 0

        hints = mm.get_hints("cables")
        assert len(hints) == 1
        assert hints[0]["hint_text"] == "Начинать с каталога"

    def test_hints_empty(self, mm):
        assert mm.get_hints("nonexistent") == []

    def test_sites_crud(self, mm):
        domain = mm.add_site("new-site.ru", "New Site", "cables")
        assert domain == "new-site.ru"

        sites = mm.get_sites("cables")
        assert any(s["id"] == "new-site.ru" for s in sites)

    def test_save_approach_with_selectors(self, mm):
        aid = mm.save_approach(
            product_type="cables", site="tinko.ru",
            concrete_steps=[{"action": "click", "selector": ".price"}],
            selectors_cache={"price": {"primary": ".price", "fallback": [".cost"]}},
        )
        assert aid > 0
        best = mm.get_best_approach("cables", "tinko.ru")
        assert "price" in best.get("selectors_cache", {})
