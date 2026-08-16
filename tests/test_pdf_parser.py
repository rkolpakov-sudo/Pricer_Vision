import pytest
from unittest.mock import MagicMock, patch

from src.pdf_parser.mineru_backend import MinerUBackend
from src.pdf_parser.structurer import SpecStructurer, _html_to_text
from src.pdf_parser.feedback import FeedbackCollector


class TestMinerUBackend:
    def test_init(self):
        b = MinerUBackend(lang="east_slavic", method="auto")
        assert b._lang == "east_slavic"
        assert b._method == "auto"

    @patch("src.pdf_parser.mineru_backend.subprocess.run")
    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_success(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            import os
            md_path = os.path.join(tmp, "test.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Test PDF\nКабель ВВГнг 4×120 — 350 м")
            with patch("src.pdf_parser.mineru_backend.tempfile.TemporaryDirectory") as mock_tmp:
                mock_tmp.return_value.__enter__.return_value = tmp
                b = MinerUBackend()
                result = b.parse("dummy.pdf")
                assert "Кабель" in result

    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_not_found(self, mock_exists):
        mock_exists.return_value = False
        b = MinerUBackend()
        with pytest.raises(FileNotFoundError):
            b.parse("nonexistent.pdf")


class TestHtmlToText:
    def test_pipe_delimited_columns(self):
        html = "<table><tr><td>БД3БД4</td><td>Вентилятор ВРм</td><td>ВРм №11,2</td><td>Globalclimat</td><td>шт</td><td>2</td><td>525</td></tr></table>"
        result = _html_to_text(html)
        assert "БД3БД4" in result
        assert "Вентилятор ВРм" in result
        assert "Globalclimat" in result
        assert "|" in result

    def test_handles_br(self):
        html = "Строка1<br>Строка2"
        assert _html_to_text(html) == "Строка1\nСтрока2"

    def test_handles_nbsp(self):
        html = "Текст&nbsp;с&nbsp;пробелами"
        assert "с пробелами" in _html_to_text(html)

    def test_handles_empty_after_strip(self):
        assert _html_to_text("<table></table>") == ""

    def test_handles_no_html(self):
        assert _html_to_text("Простой текст") == "Простой текст"

    def test_multiple_rows(self):
        html = """<table>
            <tr><td>A1</td><td>B1</td></tr>
            <tr><td>A2</td><td>B2</td></tr>
        </table>"""
        result = _html_to_text(html)
        assert "A1 | B1" in result
        assert "A2 | B2" in result


class TestSpecStructurer:
    @pytest.mark.asyncio
    async def test_structure_valid(self):
        mock_llm = MagicMock()
        s = SpecStructurer(mock_llm)
        items = await s.structure("1. Кабель ВВГнг 4×120 — 350 м\n2. Труба ПНД 32 — 200 м")
        assert len(items) == 2
        assert items[0]["name"] == "Кабель ВВГнг 4×120"
        assert items[0]["qty"] == 350.0
        assert items[0].get("manufacturer") == ""
        assert items[0].get("code") == ""
        assert items[1]["name"] == "Труба ПНД 32"

    @pytest.mark.asyncio
    async def test_structure_with_all_fields(self):
        mock_llm = MagicMock()
        s = SpecStructurer(mock_llm)
        items = await s.structure("БД3, БД4 | Вентилятор ВРм №11,2 РВ9-дУ | ВРм №11,2 РВ9-ДУ | Globalclimat или аналог | шт | 2 | 525")
        assert len(items) >= 1
        assert items[0]["manufacturer"] != ""
        assert items[0]["code"] != ""
        assert items[0]["qty"] == 2.0
        assert items[0]["unit"] == "шт"

    @pytest.mark.asyncio
    async def test_structure_empty(self):
        s = SpecStructurer(MagicMock())
        items = await s.structure("")
        assert items == []

    @pytest.mark.asyncio
    async def test_structure_fallback(self):
        s = SpecStructurer(MagicMock())
        items = await s.structure("1. Кабель 100 м\n2. Труба 200 шт")
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_structure_strips_html(self):
        mock_llm = MagicMock()
        s = SpecStructurer(mock_llm)
        html = "<table><tr><td>1. Кабель ВВГнг 4×120 — 350 м</td></tr></table>"
        items = await s.structure(html)
        assert len(items) >= 1
        assert "Кабель" in items[0]["name"]

    @pytest.mark.asyncio
    async def test_structure_pipe_delimited(self):
        mock_llm = MagicMock()
        s = SpecStructurer(mock_llm)
        html = "<tr><td>БД3, БД4</td><td>Вентилятор ВРм №11,2 РВ9-дУ</td><td>ВРм №11,2 РВ9-ДУ</td><td>Globalclimat или аналог</td><td>шт</td><td>2</td><td>525</td></tr>"
        items = await s.structure(html)
        assert len(items) >= 1
        assert "Вентилятор" in items[0]["name"]
        assert items[0]["code"] != ""
        assert items[0]["manufacturer"] != ""

    @pytest.mark.asyncio
    async def test_structure_only_html(self):
        s = SpecStructurer(MagicMock())
        items = await s.structure("<table></table>")
        assert items == []

    def test_fallback_parse(self):
        s = SpecStructurer.__new__(SpecStructurer)
        items = s._fallback_parse("1. Кабель ВВГнг 4×120 — 350 м\n2. Труба ПНД 32 — 200 м")
        assert len(items) >= 1


class TestFeedbackCollector:
    def test_table_creation(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        fc = FeedbackCollector(db_path)
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            assert ("pdf_corrections",) in tables

    def test_save_and_get_correction(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        fc = FeedbackCollector(db_path)
        fc.save_correction("Кабель ВВГнг", "Кабель ВВГнг 4×120", "manual")
        result = fc.get_correction("Кабель ВВГнг")
        assert result == "Кабель ВВГнг 4×120"

    def test_no_correction(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        fc = FeedbackCollector(db_path)
        result = fc.get_correction("nonexistent")
        assert result is None

    def test_apply_corrections(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        fc = FeedbackCollector(db_path)
        fc.save_correction("Старый текст", "Новый текст")
        items = [{"pos": 1, "name": "Старый текст", "specs": "", "code": "", "manufacturer": "", "qty": 1, "unit": "шт", "weight": 0}]
        corrected = fc.apply_corrections(items)
        assert corrected[0]["name"] == "Новый текст"
        assert corrected[0].get("_corrected")

    def test_stats(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        fc = FeedbackCollector(db_path)
        fc.save_correction("A", "B")
        fc.save_correction("C", "D", "manual")
        stats = fc.get_stats()
        assert stats["total_corrections"] == 2
