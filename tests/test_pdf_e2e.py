# -*- coding: utf-8 -*-
"""E2E pdf-pipeline tests on real fixture files.

Fast-path тесты идут через настоящий pdf-inspector (<1 с на файл).
MinerU-эскалация (~35 с) — только при PDF_E2E_MINERU=1.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from src.pdf_parser.fast_backend import FastBackend, route_pdf
from src.pdf_parser.structurer import SpecStructurer

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"
EXPECTED = json.loads((FIXTURES / "expected_items.json").read_text(encoding="utf-8"))


def _pipeline(pdf_path: Path):
    """classify -> route -> extract -> structurer (зеркало runner._run без Qt)."""
    fast = FastBackend()
    cls = fast.classify(str(pdf_path))
    route = route_pdf(cls)
    md = ""
    if route == "fast":
        md = fast.extract_markdown(str(pdf_path))
        if len(md.strip()) < 100:
            route = "mineru-escalate"
    items = []
    if md:
        s = SpecStructurer(llm_client=None)
        items = asyncio.run(s.structure(md))
    return cls, route, md, items


class TestFastPathE2E:
    @pytest.mark.parametrize("name", [
        "table_classic.pdf",
        "table_no_weight.pdf",
        "table_unit_qty_merged.pdf",
        "gost_in_name.pdf",
    ])
    def test_born_digital_spec(self, name):
        cls, route, md, items = _pipeline(FIXTURES / name)
        assert cls["pdf_type"] == "text_based"
        assert route == "fast"
        exp = EXPECTED[name]
        assert len(items) == len(exp)
        for got, want in zip(items, exp):
            assert got["qty"] == float(want["qty"])
            assert want["name"].lower()[:15] in got["name"].lower()

    def test_scan_routes_to_mineru(self):
        cls, route, _, _ = _pipeline(FIXTURES / "scan_spec.pdf")
        assert cls["pdf_type"] == "scanned"
        assert route == "mineru"

    def test_broken_fonts_escalate_by_short_text(self):
        """Битые шрифты: enc_issues молчит, но текст деградирует — гейт длины."""
        cls, route, _, _ = _pipeline(FIXTURES / "broken_fonts.pdf")
        assert route == "mineru-escalate"


@pytest.mark.skipif(
    os.environ.get("PDF_E2E_MINERU") != "1",
    reason="полный прогон MinerU ~35 c; включается PDF_E2E_MINERU=1",
)
@pytest.mark.skipif(
    not (Path(__file__).parents[1] / "mineru_venv" / "Scripts" / "mineru.exe").exists(),
    reason="mineru_venv не найден",
)
class TestScanEscalationE2E:
    def test_scan_through_real_mineru(self):
        from src.pdf_parser.mineru_backend import MinerUBackend

        pdf = FIXTURES / "scan_spec.pdf"
        backend = MinerUBackend(lang="east_slavic", method="auto")
        md = asyncio.run(backend.parse_async(str(pdf), timeout=300))
        items = asyncio.run(SpecStructurer(llm_client=None).structure(md))
        qtys = {it["qty"] for it in items}
        assert {10.0, 120.5} <= qtys