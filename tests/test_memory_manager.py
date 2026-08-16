import pytest
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager


@pytest.fixture
def mm(graph_engine):
    return MemoryManager(graph_engine)


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
        pid = mm.save_price("test", "cables", "tinko.ru", 100, "http://x.ru", 0.5)
        assert pid == 0

    def test_save_price_above_threshold(self, mm):
        pid = mm.save_price("test item", "cables", "tinko.ru", 1500, "http://x.ru", 0.95)
        assert pid > 0

    def test_get_relevant_prices(self, mm):
        mm.save_price("ВВГ-нг 3x1.5 ОГНЕСТОЙКИЙ КАБЕЛЬ", "cables", "tinko.ru", 100, "http://x.ru", 0.95)
        prices = mm.get_relevant_prices("ВВГ-нг 3x2.5 ОГНЕСТОЙКИЙ КАБЕЛЬ")
        assert len(prices) >= 1

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
