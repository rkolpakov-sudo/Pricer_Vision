"""Тесты ExcelWriter (Qt-free, openpyxl) — загрузка спецификаций, детект колонок, запись результатов."""

from pathlib import Path

import openpyxl
import pytest

from src.excel_writer import ExcelWriter, SpecItem


def make_spec_xlsx(tmp_path, headers, rows):
    path = tmp_path / "spec.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


@pytest.fixture
def writer(tmp_path):
    path = make_spec_xlsx(
        tmp_path,
        ["№", "Наименование", "Марка", "Артикул", "Кол-во", "Ед."],
        [
            [1, "Кабель ВВГ", "Спецкабель", "ВВГ-3x2.5", 100, "м"],
            [2, "Труба ПНД", "ОАО", "", 50, "шт"],
        ],
    )
    w = ExcelWriter({"paths": {"data_output": str(tmp_path / "out")}})
    w.load_spec(path)
    return w


class TestSpecItem:
    def test_defaults(self):
        item = SpecItem("text")
        assert item.text == "text"
        assert item.article == ""
        assert item.brand == ""
        assert item.uom == "шт"
        assert item.headers == []

    def test_full(self):
        item = SpecItem("t", article="a", brand="b", name_raw="n", uom="м", headers=["h"])
        assert item.article == "a"
        assert item.uom == "м"


class TestLoadSpec:
    def test_counts_data_rows(self, writer):
        assert writer.total_rows == 2

    def test_headers_loaded(self, writer):
        assert writer.headers[1] == "Наименование"
        assert writer.headers[4] == "Кол-во"

    def test_properties(self, writer):
        assert writer.ws is not None
        assert writer.header_map is not None
        assert writer.spec_path.endswith(".xlsx")

    def test_empty_workbook_properties(self):
        w = ExcelWriter({})
        assert w.headers == []
        assert w.ws is None
        assert w.spec_path is None
        assert w.total_rows == 0


class TestDetectColumns:
    def test_typical(self):
        w = ExcelWriter({})
        mapping = w.detect_columns(["Наименование", "Артикул", "Марка", "Кол-во", "Ед."])
        assert mapping["name"] == [0]
        assert mapping["article"] == [1]
        assert mapping["brand"] == [2]
        assert mapping["qty"] == 3
        assert mapping["uom"] == 4

    def test_brand_not_name(self):
        w = ExcelWriter({})
        mapping = w.detect_columns(["Наименование", "Производитель"])
        assert mapping["brand"] == [1]
        assert mapping["name"] == [0]

    def test_no_name_fallback(self):
        w = ExcelWriter({})
        mapping = w.detect_columns(["Артикул", "Цена"])
        assert mapping["name"]

    def test_none_header_skipped(self):
        w = ExcelWriter({})
        mapping = w.detect_columns([None, "Кол-во"])
        assert mapping["name"] == []


class TestBuildItemName:
    def test_combines_brand_and_name(self, writer):
        mapping = writer.detect_columns(writer.headers)
        name, uom, article = writer.build_item_name(2, mapping)
        # производитель НЕ конкатенируется в имя (держится отдельно)
        assert name == "Кабель ВВГ"
        assert uom == "м"
        assert article == "ВВГ-3x2.5"
        brand = writer._concat_cells(2, mapping.get("brand", []))
        assert brand == "Спецкабель"

    def test_no_ws(self):
        w = ExcelWriter({})
        assert w.build_item_name(1, {"article": [], "brand": [], "name": [], "uom": None}) == ("", "шт", None)


class TestGetSpecs:
    def test_builds_specs_skipping_empty(self, tmp_path):
        path = make_spec_xlsx(tmp_path, ["Наименование"], [["Кабель"], [], ["Труба"]])
        w = ExcelWriter({})
        w.load_spec(path)
        specs = w.get_specs()
        assert len(specs) == 2
        assert specs[0].text == "Кабель"
        assert specs[0].uom == "шт"

    def test_empty_ws(self):
        w = ExcelWriter({})
        assert w.get_specs() == []


class TestWriteAndSave:
    def test_output_headers_added(self, writer):
        hm = writer.header_map
        assert writer.ws.cell(1, hm["price"]).value == "Цена, RUB"
        assert writer.ws.cell(1, hm["url"]).value == "URL"
        assert writer.ws.cell(1, hm["category"]).value == "Категория"

    def test_existing_output_headers_reused(self, tmp_path):
        path = make_spec_xlsx(tmp_path, ["Наименование", "Цена, RUB"], [["X", 100]])
        w = ExcelWriter({})
        w.load_spec(path)
        hm = w.header_map
        assert hm["price"] == 2

    def test_write_result(self, writer):
        writer.write_result(2, {
            "final_price_rub": 1500.5, "card_url": "http://x.ru",
            "primary_cat": "cables", "primary_subcat": "power",
        })
        hm = writer.header_map
        assert writer.ws.cell(2, hm["price"]).value == 1500.5
        assert writer.ws.cell(2, hm["url"]).value == "http://x.ru"
        assert "power" in writer.ws.cell(2, hm["category"]).value

    def test_write_result_no_state(self, writer):
        writer.write_result(2, {})
        hm = writer.header_map
        assert writer.ws.cell(2, hm["price"]).value is None

    def test_write_result_no_ws(self):
        w = ExcelWriter({})
        w.write_result(1, {"final_price_rub": 1})  # no crash

    def test_save_output_copy(self, writer, tmp_path):
        out_path = writer.save_output_copy(str(tmp_path / "out"))
        assert out_path.endswith(".xlsx")
        assert Path(out_path).exists()

    def test_save_output_copy_no_path(self, tmp_path):
        w = ExcelWriter({})
        assert w.save_output_copy(str(tmp_path)) == ""

    def test_flush(self, writer, tmp_path):
        writer.flush()
        out = list((tmp_path / "out").glob("*.xlsx"))
        assert len(out) >= 1
