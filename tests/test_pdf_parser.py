import asyncio
import subprocess

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.pdf_parser.mineru_backend import MinerUBackend
from src.pdf_parser.ocr_fallback import OCRFallback
from src.pdf_parser.review import SmartReview
from src.pdf_parser.structurer import SpecStructurer, _html_to_text, _extract_llm_content
from src.pdf_parser.feedback import FeedbackCollector


def _fake_subprocess_proc(returncode=0, stdout=b"", stderr=b"", hang=False):
    proc = AsyncMock()
    proc.returncode = returncode

    class _Stream:
        def __init__(self, data):
            self._data = data
            self._done = False

        async def read(self, n=-1):
            if hang:
                await asyncio.sleep(5)
            if self._done:
                return b""
            self._done = True
            return self._data

    proc.stdout = _Stream(stdout)
    proc.stderr = _Stream(stderr)
    return proc


class TestMinerUBackend:
    def test_init(self):
        b = MinerUBackend(lang="east_slavic", method="auto")
        assert b._lang == "east_slavic"
        assert b._method == "auto"

    @patch("src.pdf_parser.mineru_backend.subprocess.Popen")
    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_success(self, mock_exists, mock_popen):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate.return_value = ("", "")
        mock_popen.return_value = proc
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

    @patch("src.pdf_parser.mineru_backend.subprocess.Popen")
    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_timeout_kills_tree(self, mock_exists, mock_popen):
        proc = MagicMock()
        proc.returncode = None
        proc.communicate.side_effect = [subprocess.TimeoutExpired("cmd", 1), ("", "")]
        mock_popen.return_value = proc
        b = MinerUBackend()
        with pytest.raises(TimeoutError):
            b.parse("dummy.pdf", timeout=1)
        # после таймаута вызывается kill дерева (taskkill /T /F)
        kill_calls = [c for c in mock_popen.call_args_list]  # noqa

    @patch("src.pdf_parser.mineru_backend.subprocess.Popen")
    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_error_raises_runtime_error(self, mock_exists, mock_popen):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate.return_value = ("", "boom")
        mock_popen.return_value = proc
        b = MinerUBackend()
        with pytest.raises(RuntimeError) as exc:
            b.parse("dummy.pdf")
        assert "boom" in str(exc.value)

    @patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True)
    def test_parse_not_found(self, mock_exists):
        mock_exists.return_value = False
        b = MinerUBackend()
        with pytest.raises(FileNotFoundError):
            b.parse("nonexistent.pdf")

    def test_stage_regex(self):
        import re
        from src.pdf_parser.mineru_backend import _STAGE_RE
        line = "Layout Predict:  66%|######5   | 38/58 [00:17<00:09,  2.07it/s]"
        matches = _STAGE_RE.findall(line)
        assert ("Layout Predict", "66") in matches
        assert _STAGE_RE.findall("no progress here") == []

    def test_emit_progress_calls_callback(self):
        b = MinerUBackend()
        events = []
        b._emit_progress(b"Layout Predict:  66%|#", lambda s, p: events.append((s, p)))
        assert events == [("Layout Predict", 66)]

    def test_kill_tree_windows(self):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345
        with patch("src.pdf_parser.mineru_backend.os.name", "nt"), \
             patch("src.pdf_parser.mineru_backend.subprocess.run") as mock_run:
            from src.pdf_parser.mineru_backend import _kill_tree
            _kill_tree(proc)
            args = mock_run.call_args[0][0]
            assert "taskkill" in args
            assert "/T" in args
            assert "/F" in args
            assert "12345" in args

    @pytest.mark.asyncio
    async def test_parse_async_success(self):
        async def fake_create_subprocess(*args, **kwargs):
            return _fake_subprocess_proc(returncode=0)
        with patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True), \
             patch("src.pdf_parser.mineru_backend.asyncio.create_subprocess_exec",
                   new=fake_create_subprocess):
            import tempfile, os
            with tempfile.TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "out.md"), "w", encoding="utf-8") as f:
                    f.write("# PDF\nКабель ВВГнг")
                with patch("src.pdf_parser.mineru_backend.tempfile.TemporaryDirectory") as mock_tmp:
                    mock_tmp.return_value.__enter__.return_value = tmp
                    b = MinerUBackend()
                    result = await b.parse_async("dummy.pdf")
                    assert "Кабель" in result

    @pytest.mark.asyncio
    async def test_parse_async_timeout_kills_tree(self):
        async def fake_create_subprocess(*args, **kwargs):
            return _fake_subprocess_proc(returncode=None, hang=True)
        with patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True), \
             patch("src.pdf_parser.mineru_backend.asyncio.create_subprocess_exec",
                   new=fake_create_subprocess), \
             patch("src.pdf_parser.mineru_backend._kill_tree") as mock_kill:
            b = MinerUBackend()
            with pytest.raises(TimeoutError):
                await b.parse_async("dummy.pdf", timeout=1)
            mock_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_async_progress_callback(self):
        events = []

        async def fake_create_subprocess(*args, **kwargs):
            return _fake_subprocess_proc(
                returncode=0, stderr=b"Layout Predict:  40%|#  | 20/58\n"
                                      b"Layout Predict:  80%|## | 40/58\n",
            )
        with patch("src.pdf_parser.mineru_backend.Path.exists", return_value=True), \
             patch("src.pdf_parser.mineru_backend.asyncio.create_subprocess_exec",
                   new=fake_create_subprocess):
            import tempfile, os
            with tempfile.TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "out.md"), "w", encoding="utf-8") as f:
                    f.write("# PDF")
                with patch("src.pdf_parser.mineru_backend.tempfile.TemporaryDirectory") as mock_tmp:
                    mock_tmp.return_value.__enter__.return_value = tmp
                    b = MinerUBackend()
                    await b.parse_async(
                        "dummy.pdf",
                        progress_callback=lambda s, p: events.append((s, p)),
                    )
        assert ("Layout Predict", 80) in events


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


class TestExtractLlmContent:
    def test_openai_envelope(self):
        response = {"choices": [{"message": {"content": "hello"}}]}
        assert _extract_llm_content(response) == "hello"

    def test_plain_content(self):
        assert _extract_llm_content({"content": "hi"}) == "hi"

    def test_error_response(self):
        assert _extract_llm_content({"error": "LLM недоступен"}) == ""

    def test_missing_fields(self):
        assert _extract_llm_content({"choices": []}) == ""

    def test_non_dict(self):
        assert _extract_llm_content("text") == ""


class TestSpecStructurerLlm:
    @pytest.mark.asyncio
    async def test_use_llm_true_returns_parsed_items(self):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {
            "choices": [{"message": {"content": '[{"pos": 1, "name": "Кабель ВВГнг", "specs": "3х2.5", "code": "A001", "manufacturer": "ООО", "qty": 100, "unit": "м", "weight": 0}]'}}]
        }
        s = SpecStructurer(mock_llm, use_llm=True)
        items = await s.structure("1. Кабель ВВГнг — 100 м")
        assert len(items) == 1
        assert items[0]["name"] == "Кабель ВВГнг"
        assert items[0]["qty"] == 100.0
        assert items[0]["code"] == "A001"
        mock_llm.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_use_llm_false_skips_llm(self):
        mock_llm = AsyncMock()
        s = SpecStructurer(mock_llm, use_llm=False)
        items = await s.structure("1. Кабель ВВГнг 4×120 — 350 м")
        assert len(items) == 1
        mock_llm.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_regex(self):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"error": "LLM недоступен"}
        s = SpecStructurer(mock_llm, use_llm=True)
        items = await s.structure("1. Кабель ВВГнг 4×120 — 350 м")
        assert len(items) >= 1
        assert items[0]["name"] == "Кабель ВВГнг 4×120"

    @pytest.mark.asyncio
    async def test_llm_empty_json_falls_back(self):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = {"choices": [{"message": {"content": "not json at all"}}]}
        s = SpecStructurer(mock_llm, use_llm=True)
        items = await s.structure("1. Кабель 100 м")
        assert len(items) >= 1

    def test_normalize_item(self):
        normalized = SpecStructurer._normalize_item(
            {"pos": "2", "name": " Труба ", "specs": "32", "code": "B2",
             "manufacturer": "ЗАО", "qty": "5.5", "unit": "шт", "weight": "1.2"})
        assert normalized == {"pos": 2, "name": "Труба", "specs": "32", "code": "B2",
                              "manufacturer": "ЗАО", "qty": 5.5, "unit": "шт", "weight": 1.2}

    def test_normalize_item_non_dict(self):
        assert SpecStructurer._normalize_item("bad") == {}


class TestOCRFallback:
    def test_needs_ocr_short_text(self):
        ocr = OCRFallback()
        assert ocr.needs_ocr("  ") is True
        assert ocr.needs_ocr("short") is True

    def test_needs_ocr_long_text(self):
        ocr = OCRFallback()
        text = "x" * 200
        assert ocr.needs_ocr(text) is False

    def test_default_backend_created(self):
        ocr = OCRFallback(lang="east_slavic", method="auto")
        assert isinstance(ocr.mineru_backend, MinerUBackend)
        assert ocr.ocr_engine is None

    @pytest.mark.asyncio
    async def test_extract_with_ocr_calls_backend(self):
        backend = MagicMock()
        backend.parse = MagicMock(return_value="extracted text")
        ocr = OCRFallback(mineru_backend=backend)
        result = await ocr.extract_with_ocr("dummy.pdf", timeout=60)
        assert result == "extracted text"
        backend.parse.assert_called_once_with("dummy.pdf", 60)

    @pytest.mark.asyncio
    async def test_extract_with_ocr_no_backend(self):
        ocr = OCRFallback(mineru_backend=None)
        ocr.mineru_backend = None
        result = await ocr.extract_with_ocr("dummy.pdf")
        assert result == ""

    @pytest.mark.asyncio
    async def test_extract_with_ocr_async_calls_backend(self):
        backend = AsyncMock()
        backend.parse_async = AsyncMock(return_value="extracted text")
        ocr = OCRFallback(mineru_backend=backend)
        result = await ocr.extract_with_ocr_async(
            "dummy.pdf", timeout=60, progress_callback=lambda s, p: None,
        )
        assert result == "extracted text"
        backend.parse_async.assert_called_once()


class TestSmartReview:
    def test_high_confidence_auto_approved(self):
        items = [
            {"pos": 1, "name": "Кабель ВВГнг", "specs": "3х2.5", "code": "A001",
             "manufacturer": "ООО", "qty": 100, "unit": "м", "weight": 0},
        ]
        review = SmartReview()
        auto, needs = review.process_extraction(items)
        assert len(auto) == 1
        assert len(needs) == 0
        assert items[0]["confidence"] == 1.0

    def test_missing_fields_needs_review(self):
        items = [
            {"pos": 1, "name": "", "specs": "", "code": "", "manufacturer": "",
             "qty": 0, "unit": "", "weight": 0},
        ]
        review = SmartReview()
        auto, needs = review.process_extraction(items)
        assert len(auto) == 0
        assert len(needs) == 1
        assert items[0]["confidence"] == 0.0

    def test_partial_split(self):
        items = [
            {"pos": 1, "name": "Труба", "qty": 100, "unit": "м", "specs": "32", "code": "", "manufacturer": "", "weight": 0},
            {"pos": 2, "name": "", "qty": 0, "unit": "", "specs": "", "code": "", "manufacturer": "", "weight": 0},
        ]
        review = SmartReview()
        auto, needs = review.process_extraction(items)
        assert len(auto) == 1
        assert len(needs) == 1

    def test_confidence_calculation(self):
        review = SmartReview()
        row = {"pos": 1, "name": "Кабель", "specs": "", "code": "", "manufacturer": "",
               "qty": 10, "unit": "м", "weight": 0}
        assert review._calculate_confidence(row) == pytest.approx(0.7)

    def test_custom_threshold(self):
        items = [
            {"pos": 1, "name": "Труба", "qty": 5, "unit": "шт", "specs": "", "code": "", "manufacturer": "", "weight": 0},
        ]
        review = SmartReview(threshold=0.9)
        auto, needs = review.process_extraction(items)
        assert len(auto) == 0
        assert len(needs) == 1


class TestConfigLoaderPdf:
    def test_get_pdf_config_from_settings(self):
        from src.config_loader import get_pdf_config
        assert get_pdf_config("use_llm", True) is False
        assert get_pdf_config("lang", "en") == "east_slavic"
        assert get_pdf_config("llm_max_tokens", 0) == 1024
        assert get_pdf_config("ocr_min_text_length", 0) == 100
        assert get_pdf_config("missing_key", "default") == "default"
