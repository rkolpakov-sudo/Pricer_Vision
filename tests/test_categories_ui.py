"""Тесты групп/категорий: конфиг special_types и UI CategoriesPage (offscreen)."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.config_loader import get_special_types


class TestSpecialTypesConfig:
    def test_reads_special_types_from_settings(self):
        st = get_special_types()
        assert "plumbing_heating_radiators" in st["radiators"]
        assert "ventilation_climate_ventilation" in st["ductwork"]


class _PanelStub:
    def __init__(self, engine):
        self._engine = engine
        self._mm = MemoryManager(engine)

    @property
    def engine(self):
        return self._engine

    @property
    def mm(self):
        return self._mm

    def refresh_all_combos(self):
        pass


@pytest.fixture
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication недоступен")


@pytest.fixture
def seeded_engine(tmp_path):
    e = GraphEngine(str(tmp_path / "t.db"))
    e.build()
    e.save_category("tools_general", "Инструменты, крепёж, изоляция", priority=6)
    e.save_product_type("tools_general", "Инструменты, крепёж, изоляция",
                        category="tools_general", keywords="изоляц, рулетка")
    e.save_product_type("plumbing_heating_pipes", "Трубы", category="plumbing_heating",
                        keywords="труба")
    e.save_category("plumbing_heating", "Трубы и арматура", priority=1)
    yield e
    e._conn.close()


class TestCategoriesPage:
    def test_load_lists_categories_and_types(self, qapp, seeded_engine):
        from gui.graph_assistant import CategoriesPage
        page = CategoriesPage(_PanelStub(seeded_engine))
        page.refresh()
        # таблица категорий заполнена
        ids = [page.cat_table.item(r, 0).text() for r in range(page.cat_table.rowCount())]
        assert "tools_general" in ids
        # выбрать tools_general → в его типы попадает tools_general
        for r in range(page.cat_table.rowCount()):
            if page.cat_table.item(r, 0).text() == "tools_general":
                page.cat_table.selectRow(r)
                break
        page._cat_selected()
        tids = [page.type_table.item(r, 0).text() for r in range(page.type_table.rowCount())]
        assert "tools_general" in tids

    def test_move_type_between_categories(self, qapp, seeded_engine):
        from gui.graph_assistant import CategoriesPage
        import gui.graph_assistant as ga
        page = CategoriesPage(_PanelStub(seeded_engine))
        page.refresh()
        # tools_general уже в категории tools_general; перенесём его в plumbing_heating
        page._current_cat = "tools_general"
        page._load_types()
        row = next(i for i in range(page.type_table.rowCount())
                   if page.type_table.item(i, 0).text() == "tools_general")
        page.type_table.selectRow(row)
        # выбрать целевую категорию plumbing_heating
        idx = page.target_combo.findData("plumbing_heating")
        assert idx >= 0
        page.target_combo.setCurrentIndex(idx)
        # Мокаем _confirm чтобы не блокировался на диалоге
        original_confirm = ga._confirm
        ga._confirm = lambda *a, **kw: True
        try:
            page._move_type()
        finally:
            ga._confirm = original_confirm
        prod = seeded_engine.get_all_products()["tools_general"]
        assert prod["category"] == "plumbing_heating"
