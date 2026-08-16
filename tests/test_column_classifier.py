"""Тесты системной классификации колонок спецификации.

Покрывают реальные заголовки ГОСТ-спецификаций и валидацию по значениям,
которые ломали старый наивный матчинг (см. issue: 1K+ позиций -> 17).
"""

import pytest

from src.column_classifier import classify_columns, ColumnMapping


def classify(headers, rows=None):
    return classify_columns(headers, value_rows=rows)


class TestRealSpecStructure:
    """Регрессия: структура реальной спецификации 08_12-23RD_K1_OV."""

    HEADERS = [
        "Позиция",
        "Наименование и техническая характеристика",
        "Тип, марка, обозначение документа, опросного листа",
        "Код оборудования, изделия, материала",
        "Завод-изготовитель",
        "Единица измерения",
        "Количество",
        "Масса единицы (кг)",
        "Примечание",
    ]

    ROWS = [
        ["1.", "Кран шаровой Ду15", "", "", '"Ридан"', "шт.", "48", "", ""],
        ["2.", "Кран шаровой Ду20", "CC11 500x400", "SPL.T.16.200.EC", '"Ридан"', "шт.", "18", "", ""],
        ["3.", "Воздуховод", "ГОСТ 14918-2020", "", "Aerostar", "м.п", "100", "", ""],
        ["4.", "Тройник", "", "", "Россия", "шт.", "2", "", "ДОУ"],
    ]

    def test_full_mapping(self):
        m = classify(self.HEADERS, self.ROWS)
        assert m.name == [1]
        assert m.article == [3]
        assert m.brand == [4]
        assert m.spec == [2]
        assert m.uom == 5
        assert m.qty == 6
        assert m.weight == 7
        assert m.position == 0
        assert m.note == 8
        assert m.unmapped == []

    def test_no_silent_loss(self):
        m = classify(self.HEADERS, self.ROWS)
        mapped = {c for c in m.name + m.article + m.brand + m.spec + m.unmapped}
        mapped |= {c for c in (m.uom, m.qty, m.weight, m.position, m.note) if c is not None}
        assert mapped == set(range(9))

    def test_brand_not_in_name(self):
        m = classify(self.HEADERS, self.ROWS)
        assert 4 not in m.name


class TestHeaderVariants:
    def test_manufacturer_variants(self):
        for header in ["Завод-изготовитель", "Изготовитель", "Производитель",
                       "Произв.", "Завод", "Фирма", "Бренд", "Марка"]:
            m = classify([header])
            assert m.brand == [0], f"brand not detected for {header!r}"

    def test_name_variants(self):
        for header in ["Наименование", "Наименование и техническая характеристика",
                       "Название", "Наименование товара", "Материал"]:
            m = classify([header])
            assert m.name == [0], f"name not detected for {header!r}"

    def test_article_variants(self):
        for header in ["Артикул", "Код оборудования", "Код оборудования, изделия, материала",
                       "Артикул/код", "Каталожный номер", "SKU"]:
            m = classify([header])
            assert m.article == [0], f"article not detected for {header!r}"

    def test_uom_variants(self):
        for header in ["Единица измерения", "Ед. изм.", "Ед.изм.", "Ед.", "Единицы"]:
            m = classify([header])
            assert m.uom == 0, f"uom not detected for {header!r}"

    def test_qty_variants(self):
        for header in ["Количество", "Кол-во", "Кол.", "Всего", "Quantity"]:
            m = classify([header])
            assert m.qty == 0, f"qty not detected for {header!r}"

    def test_weight_variants(self):
        for header in ["Масса единицы (кг)", "Масса", "Вес единицы"]:
            m = classify([header])
            assert m.weight == 0, f"weight not detected for {header!r}"

    def test_position_variants(self):
        for header in ["Позиция", "№", "№ п/п", "Номер п/п", "Поз."]:
            m = classify([header])
            assert m.position == 0, f"position not detected for {header!r}"

    def test_note_variants(self):
        for header in ["Примечание", "Примечания", "Note"]:
            m = classify([header])
            assert m.note == 0, f"note not detected for {header!r}"


class TestAmbiguityResolution:
    def test_code_column_not_name(self):
        """«Код … материала» → article, а не name (ложное срабатывание «материал»)."""
        headers = ["Наименование", "Код оборудования, изделия, материала"]
        m = classify(headers)
        assert m.name == [0]
        assert m.article == [1]

    def test_type_mark_column_is_spec_not_brand(self):
        headers = ["Тип, марка, обозначение документа"]
        m = classify(headers)
        assert m.spec == [0]
        assert m.brand == []

    def test_mass_not_uom_by_values(self):
        """«Масса единицы (кг)» с числовыми значениями → weight, а не uom."""
        headers = ["Единица измерения", "Масса единицы (кг)"]
        rows = [["шт.", "12.5"], ["м", "3.0"]]
        m = classify(headers, rows)
        assert m.uom == 0
        assert m.weight == 1

    def test_uom_confirmed_by_values(self):
        headers = ["Наименование", "Ед."]
        rows = [["Кабель", "м"], ["Труба", "шт."]]
        m = classify(headers, rows)
        assert m.uom == 1

    def test_numeric_column_is_qty(self):
        headers = ["Наименование", "Кол."]
        rows = [["А", "1"], ["Б", "2"]]
        m = classify(headers, rows)
        assert m.qty == 1

    def test_position_by_values(self):
        headers = ["Наименование", "1"]
        rows = [["А", "1."], ["Б", "2."]]
        m = classify(headers, rows)
        assert m.position == 1


class TestFallbacks:
    def test_none_headers_skipped(self):
        m = classify([None, "Кол-во"])
        assert m.name == []
        assert m.qty == 1

    def test_name_fallback_to_unassigned(self):
        m = classify(["Артикул", "Цена"])
        assert m.article == [0]
        assert m.name == [1]

    def test_empty_headers(self):
        m = classify([])
        assert m.unmapped == []

    def test_unknown_column_unmapped(self):
        m = classify(["Наименование", "Неопознанная колонка"])
        assert m.name == [0]
        assert 1 in m.unmapped

    def test_normalization_quotes_and_case(self):
        m = classify(['"Завод-изготовитель"', "  Артикул  "])
        assert m.brand == [0]
        assert m.article == [1]


class TestColumnMapping:
    def test_as_dict_shape(self):
        m = ColumnMapping(name=[0], brand=[1], uom=2, qty=3)
        d = m.as_dict()
        assert d["name"] == [0]
        assert d["brand"] == [1]
        assert d["uom"] == 2
        assert d["qty"] == 3
        assert d["spec"] == []

    def test_describe(self):
        m = ColumnMapping(name=[0], brand=[1])
        s = m.describe()
        assert "name=[0]" in s
        assert "brand=[1]" in s

    def test_describe_empty(self):
        assert ColumnMapping().describe() == "empty"
