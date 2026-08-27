"""Тесты расчёта цены радиатора по секциям (src/radiator_section_pricer.py).

ТОЛЬКО для радиаторов с суффиксом -0,9-N. Не для кранов, труб, не для радиаторов
без явного указания секций.
"""

import pytest
from src.radiator_section_pricer import extract_sections, calculate_radiator_price


class TestExtractSections:
    def test_extracts_sections(self):
        assert extract_sections("МС-140 Мх500-0,9-4") == 4
        assert extract_sections("МС-140 Мх500-0,9-2") == 2
        assert extract_sections("МС-140 Мх500-0,9-7") == 7

    def test_returns_none_without_section(self):
        assert extract_sections("Кран шаровой Ду15") is None
        assert extract_sections("Радиатор стальной панельный LEMAX") is None

    def test_empty_text(self):
        assert extract_sections("") is None
        assert extract_sections(None) is None


class TestCalculateRadiatorPrice:
    def test_rare_calculates_for_2_sections(self, graph_engine):
        """Регрессия row36: 2 секции, цена есть в БД для 4 секций → расчёт."""
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        # Убедимся, что в БД есть базовая цена
        mm.save_price(
            spec_text="Чугунный секционный радиатор с боковым подключением, тип МС-140 Мx500 (Радиатор секционный чугунный МС-140х500) МС-140 Мх500-0,9-4",
            product_type="plumbing_heating_radiators",
            site="santech.ru",
            price=5636.8,
            url="https://www.santech.ru/catalog/434/442/i498/v3/",
            confidence=0.95,
            reason="test",
        )
        result = calculate_radiator_price(
            mm,
            "Чугунный секционный радиатор с боковым подключением, тип МС-140 Мx500 МС-140 Мх500-0,9-2",
            "plumbing_heating_radiators",
        )
        assert result is not None
        assert abs(result["price"] - 2818.4) < 0.01
        assert result["confidence"] == 0.70
        assert "рассчитано программно" in result["reason"]

    def test_ignores_valves(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        assert calculate_radiator_price(mm, "Кран шаровой Ду15", "plumbing_heating_valves_armature") is None

    def test_ignores_radiator_without_sections(self, graph_engine):
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        assert calculate_radiator_price(
            mm, "Радиатор стальной панельный LEMAX Premium C10 500x600",
            "plumbing_heating_radiators"
        ) is None

    def test_returns_none_without_base_price(self, graph_engine):
        """Нет в БД подходящей цены для той же модели — расчёт невозможен."""
        from src.memory_manager import MemoryManager
        mm = MemoryManager(graph_engine)
        # Полностью уникальный товар, которого нет в БД
        result = calculate_radiator_price(
            mm,
            "Радиатор чугунный секционный МС-100 Мх400-0,9-3",
            "plumbing_heating_radiators",
        )
        assert result is None