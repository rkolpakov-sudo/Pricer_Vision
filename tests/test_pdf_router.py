# -*- coding: utf-8 -*-
"""Tests for fast_backend routing (Qt-free, pdf-inspector faked)."""
import pytest

from src.pdf_parser import fast_backend as fb
from src.pdf_parser.fast_backend import FastBackend, FastBackendError, route_pdf


class TestRoutePdf:
    def test_text_based_routes_fast(self):
        cls = {"pdf_type": "text_based", "confidence": 1.0, "encoding_issues": False}
        assert route_pdf(cls) == "fast"

    def test_scanned_routes_mineru(self):
        cls = {"pdf_type": "scanned", "confidence": 0.95, "encoding_issues": False}
        assert route_pdf(cls) == "mineru"

    def test_image_based_routes_mineru(self):
        cls = {"pdf_type": "image_based", "confidence": 1.0, "encoding_issues": False}
        assert route_pdf(cls) == "mineru"

    def test_mixed_routes_mineru(self):
        cls = {"pdf_type": "mixed", "confidence": 1.0, "encoding_issues": False}
        assert route_pdf(cls) == "mineru"

    def test_low_confidence_routes_mineru(self):
        cls = {"pdf_type": "text_based", "confidence": 0.5, "encoding_issues": False}
        assert route_pdf(cls) == "mineru"

    def test_encoding_issues_route_mineru(self):
        cls = {"pdf_type": "text_based", "confidence": 1.0, "encoding_issues": True}
        assert route_pdf(cls) == "mineru"

    def test_disabled_routes_mineru(self):
        cls = {"pdf_type": "text_based", "confidence": 1.0, "encoding_issues": False}
        assert route_pdf(cls, fast_enabled=False) == "mineru"

    def test_package_missing_routes_mineru(self, monkeypatch):
        monkeypatch.setattr(fb, "AVAILABLE", False)
        cls = {"pdf_type": "text_based", "confidence": 1.0, "encoding_issues": False}
        assert route_pdf(cls) == "mineru"


class TestFastBackend:
    def _fake_pi(self, monkeypatch, detect_result=None, process_result=None):
        class FakeDetect:
            pdf_type = "text_based"
            confidence = 1.0
            has_encoding_issues = False
            pages_needing_ocr = []
            page_count = 3

        class FakeProcess:
            markdown = "| 1 | Кран Ду15 | шт | 10 |"

        fake = type("FakePI", (), {})()
        fake.detect_pdf = lambda path: detect_result or FakeDetect()
        fake.process_pdf = lambda path: process_result or FakeProcess()
        monkeypatch.setattr(fb, "_pi", fake)
        monkeypatch.setattr(fb, "AVAILABLE", True)

    def test_classify_returns_dict(self, monkeypatch):
        self._fake_pi(monkeypatch)
        info = FastBackend().classify("x.pdf")
        assert info["pdf_type"] == "text_based"
        assert info["page_count"] == 3
        assert info["encoding_issues"] is False

    def test_extract_markdown(self, monkeypatch):
        self._fake_pi(monkeypatch)
        md = FastBackend().extract_markdown("x.pdf")
        assert "Кран Ду15" in md

    def test_error_wrapped(self, monkeypatch):
        def boom(path):
            raise RuntimeError("broken pdf")

        self._fake_pi(monkeypatch)
        monkeypatch.setattr(fb, "_pi",
                            type("F", (), {"detect_pdf": staticmethod(boom)})())
        with pytest.raises(FastBackendError):
            FastBackend().classify("x.pdf")

    def test_unavailable_raises(self):
        fb_available = fb.AVAILABLE
        try:
            fb.AVAILABLE = False
            backend = FastBackend()
            if not backend.available():
                with pytest.raises(FastBackendError):
                    backend.classify("x.pdf")
        finally:
            fb.AVAILABLE = fb_available
