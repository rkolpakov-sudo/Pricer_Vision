# -*- coding: utf-8 -*-
"""Regression tests for structurer fixes (P1 позиция/масса, P2 якорный разбор)."""
import pytest

from src.pdf_parser.structurer import SpecStructurer, _html_to_text


@pytest.fixture
def s():
    return SpecStructurer(llm_client=None)


class TestPipePositionFix:
    """P1: номер позиции не должен попадать в qty/weight."""

    def test_separate_position_column_no_weight(self, s):
        row = '| 1 | Кран шаровой Ду15 | "Ридан" | шт | 48 | |'
        item = s._parse_pipe_line(row)
        assert item is not None
        assert item["qty"] == 48.0
        assert item["unit"] == "шт"
        assert item["weight"] == 0.0
        assert "Кран шаровой" in item["name"]

    def test_glued_position_no_weight(self, s):
        row = '| 1 Кран шаровой Ду15 Ру16 | "Ридан" | шт | 10 |'
        item = s._parse_pipe_line(row)
        assert item is not None
        assert item["pos"] == 1
        assert item["name"].startswith("Кран")
        assert item["qty"] == 10.0

    def test_classic_with_weight(self, s):
        row = '| 2 Труба стальная ВГП Ду50х3.5 ГОСТ 10704-91 || м | 120.5 | 4.38 |'
        item = s._parse_pipe_line(row)
        assert item is not None
        assert item["qty"] == 120.5
        assert item["weight"] == 4.38
        assert item["unit"] == "м"

    def test_merged_unit_qty_cell(self, s):
        row = '| Кран шаровой Ду15 | "Ридан" | шт. 48 |'
        item = s._parse_pipe_line(row)
        assert item is not None
        assert item["qty"] == 48.0
        assert item["unit"] == "шт"


class TestGostNotQty:
    """P2: числа внутри наименования не становятся количеством."""

    def test_gost_in_name_pipe(self, s):
        row = '| 2 Труба стальная электросварная Ду100х4 ГОСТ 10704-91 | м | 85 |'
        item = s._parse_pipe_line(row)
        assert item is not None
        assert item["qty"] == 85.0
        assert "ГОСТ 10704-91" in item["name"]

    def test_gost_in_name_plain_unit_first(self, s):
        items = s._fallback_parse("2 Труба стальная ВГП Ду50х3.5 ГОСТ 10704-91 м 120.5\n")
        assert len(items) == 1
        assert items[0]["qty"] == 120.5
        assert items[0]["unit"] == "м"

    def test_plain_line_qty_then_unit(self, s):
        items = s._fallback_parse("3 Кабель ВВГнг(А)-LS 3х2.5 м 450\n")
        assert len(items) == 1
        assert items[0]["qty"] == 450.0
        assert items[0]["unit"] == "м"


class TestMergedRows:
    """Склеенные экстрактором строки: несколько позиций в общих ячейках."""

    def test_separate_unit_qty_cells(self, s):
        row = ('|1 Кран шаровой Ду15 Ру16 2 Воздухоотводчик автоматический Ду20'
               '|"Ридан"|шт шт|48 12|')
        items = s._parse_pipe_row(row)
        assert len(items) == 2
        assert items[0]["name"].startswith("Кран шаровой")
        assert items[0]["qty"] == 48.0
        assert items[1]["name"].startswith("Воздухоотводчик")
        assert items[1]["qty"] == 12.0
        assert all(i["unit"] == "шт" for i in items)
        assert all(i["manufacturer"] == '"Ридан"' for i in items)

    def test_pairs_cell(self, s):
        row = '|1 Насос циркуляционный Ду25 2 Кран шаровой Ду32|"Ридан"|шт. 5 шт. 7|'
        items = s._parse_pipe_row(row)
        assert len(items) == 2
        assert items[0]["qty"] == 5.0
        assert items[1]["qty"] == 7.0

    def test_single_rows_still_work(self, s):
        row = '| 1 Кран шаровой Ду15 | "Ридан" | шт | 10 |'
        items = s._parse_pipe_row(row)
        assert len(items) == 1
        assert items[0]["qty"] == 10.0


class TestHtmlCells:
    def test_br_inside_cell_does_not_break_row(self):
        html = ("<table><tr><td>Клапан балансировочный<br>авт. Ду32</td>"
                "<td>Ридан</td><td>шт</td><td>9</td></tr></table>")
        text = _html_to_text(html)
        assert text.count("\n") <= 2  # строка таблицы осталась одной строкой
        assert "|" in text and "Ду32" in text

    def test_full_html_row_parses(self, s):
        html = ("<table><tr><td>5 Клапан балансировочный<br>авт. Ду100</td>"
                "<td>\"Ридан\"</td><td>шт</td><td>2</td></tr></table>")
        items = s._fallback_parse(_html_to_text(html))
        assert len(items) == 1
        assert items[0]["qty"] == 2.0
        assert "Клапан балансировочный" in items[0]["name"]
        assert items[0]["manufacturer"] == '"Ридан"'


class TestNoTruncation:
    def test_items_beyond_24k_extracted(self, s):
        filler = "Заголовок раздела без позиции\n" * 2000  # ~54k символов
        tail = "| 99 Кабель ВВГнг 3х2.5 | м | 450 |\n"
        items = s._fallback_parse(filler + tail)
        assert any(i["name"].startswith("99 Кабель") or i["qty"] == 450.0 for i in items)

    @pytest.mark.asyncio
    async def test_structure_full_text_fallback(self, s):
        filler = "Раздел без позиций\n" * 3000
        tail = "| 7 Насос циркуляционный Ду25 | шт | 3 |\n"
        items = await s.structure(filler + tail)
        assert any(i["qty"] == 3.0 for i in items)
