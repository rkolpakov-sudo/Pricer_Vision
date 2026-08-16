import json
import pytest

from src.task_scheduler import TaskScheduler, ProcessingBatch
from src.graph_engine import GraphEngine


class FakeSpec:
    def __init__(self, text):
        self.text = text


class FakeMM:
    def __init__(self, engine):
        self._engine = engine

    def get_sites(self, product_type):
        return self._engine.get_sites_for_product(product_type)

    def get_approaches_by_site(self, site):
        return self._engine.get_approaches_by_site(site)

    def get_all_sites(self):
        return self._engine.get_all_sites()


@pytest.fixture
def scheduler(tmp_db):
    engine = GraphEngine(tmp_db)
    engine.build()
    engine.save_product_type("cables", "Кабели", "electrical", "кабель, ВВГ")
    engine.save_product_type("ups", "ИБП", "electrical", "ибп, источник бесперебойного питания")
    engine.save_discovered_site("tinko.ru", "Тинко", "cables")
    engine.save_discovered_site("keaz.ru", "КЭАЗ", "cables")
    engine.save_discovered_site("volt.ru", "Вольт", "ups")
    mm = FakeMM(engine)
    yield TaskScheduler(mm)
    engine._conn.close()


def test_batches_grouped_by_site(scheduler):
    products = [
        FakeSpec("ВВГнг 3x1.5 кабель"),
        FakeSpec("ВВГнг 3x2.5 кабель"),
        FakeSpec("ИБП источники бесперебойного питания"),
    ]
    batches = scheduler.plan_processing_order(products)
    # Кабели — один сайт, ИБП — другой
    assert len(batches) == 2
    by_site = {b.site_id: len(b.products) for b in batches}
    assert by_site.get("tinko.ru", 0) == 2
    assert by_site.get("volt.ru", 0) == 1


def test_same_site_products_in_one_batch(scheduler):
    products = [
        FakeSpec("ВВГнг 3x1.5 кабель"),
        FakeSpec("ВВГнг 3x2.5 кабель"),
        FakeSpec("ВВГнг 3x4 кабель"),
    ]
    batches = scheduler.plan_processing_order(products)
    largest = max(batches, key=lambda b: len(b.products))
    assert largest.site_id in ("tinko.ru", "keaz.ru")
    assert len(largest.products) == 3


def test_unknown_product_falls_back_to_yandex(scheduler):
    batches = scheduler.plan_processing_order([FakeSpec("неопознанный товар")])
    assert batches[0].site_id == "yandex.ru"


def test_ordered_specs_returns_all(scheduler):
    products = [FakeSpec("ВВГнг 3x1.5 кабель"), FakeSpec("неопознанный товар")]
    ordered = scheduler.ordered_specs(products)
    assert len(ordered) == 2
    assert set(s.text for s in ordered) == {"ВВГнг 3x1.5 кабель", "неопознанный товар"}


def test_batch_site_url(scheduler):
    batches = scheduler.plan_processing_order([FakeSpec("ВВГнг 3x1.5 кабель")])
    for b in batches:
        assert b.site_url.startswith("https://")


def test_priority_reflects_success_rate():
    mm = object()
    s = TaskScheduler(mm)
    low = s._calculate_priority({"success_rate": 0.2, "has_antibot": False, "speed_score": 0.5}, 1)
    high = s._calculate_priority({"success_rate": 1.0, "has_antibot": False, "speed_score": 0.5}, 10)
    assert high > low


def test_get_site_profile_success_rate(scheduler):
    engine = scheduler.mm._engine
    engine.save_approach({
        "product_type_id": "cables", "site_id": "tinko.ru",
        "pattern": [{"action": "navigate"}],
        "concrete": [{"action": "navigate", "url": "https://tinko.ru"}],
    })
    profile = scheduler._get_site_profile("tinko.ru")
    assert "success_rate" in profile
    assert "has_antibot" in profile
    assert "speed_score" in profile


def test_processing_batch_dataclass():
    b = ProcessingBatch(site_id="x.ru", site_url="https://x.ru", products=[1, 2], priority=0.9)
    assert b.site_id == "x.ru"
    assert len(b.products) == 2
    assert b.priority == 0.9
