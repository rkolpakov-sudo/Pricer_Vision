import pytest
from src.validator import validate_result, format_price


class TestValidateResult:
    def test_none_price(self):
        r = validate_result({"price": None})
        assert r["price"] is None
        assert r["confidence"] == 0.0
        assert r["requires_review"] is True

    def test_empty_price(self):
        r = validate_result({"price": "", "url": "", "site": ""})
        assert r["price"] is None
        assert r["requires_review"] is True

    def test_valid_price(self):
        r = validate_result({"price": 1500.0, "confidence": 0.95, "url": "http://x.ru", "site": "x.ru"})
        assert r["price"] == 1500.0
        assert not r["requires_review"]

    def test_low_confidence(self):
        r = validate_result({"price": 100, "confidence": 0.3})
        assert r["requires_review"] is True

    def test_high_confidence_auto_review(self):
        r = validate_result({"price": 100, "confidence": 0.9, "url": "http://x.ru", "site": "x.ru"})
        assert not r["requires_review"]

    def test_edge_confidence_still_review(self):
        r = validate_result({"price": 100, "confidence": 0.6, "url": "http://x.ru", "site": "x.ru"})
        assert r["requires_review"] is True

    def test_negative_price(self):
        r = validate_result({"price": -10, "confidence": 0.9})
        assert r["price"] is None
        assert r["confidence"] == 0.0

    def test_zero_price(self):
        r = validate_result({"price": 0, "confidence": 0.9})
        assert r["price"] is None

    def test_string_price(self):
        r = validate_result({"price": "not_a_number", "confidence": 0.9})
        assert r["price"] is None

    def test_price_as_string_number(self):
        r = validate_result({"price": "2500", "confidence": 0.9})
        assert r["price"] == 2500.0

    def test_anomalous_high_price(self):
        r = validate_result({"price": 50_000_000, "confidence": 1.0})
        assert r["confidence"] < 1.0
        assert r["requires_review"] is True

    def test_suspicious_low_price(self):
        r = validate_result({"price": 0.5, "confidence": 1.0})
        assert r["confidence"] < 1.0
        assert r["requires_review"] is True

    def test_confidence_clamped(self):
        r = validate_result({"price": 100, "confidence": 1.5})
        assert r["confidence"] == 1.0

    def test_confidence_floor(self):
        r = validate_result({"price": 100, "confidence": -0.5})
        assert r["confidence"] == 0.0

    def test_missing_keys_defaults(self):
        r = validate_result({})
        assert r["price"] is None
        assert r["confidence"] == 0.0
        assert r["url"] == ""
        assert r["site"] == ""


class TestFormatPrice:
    def test_none(self):
        assert format_price(None) == ""

    def test_less_than_thousand(self):
        assert format_price(999.99) == "999.99"

    def test_thousands(self):
        assert format_price(1500.0) == "1 500.00"

    def test_large_number(self):
        assert format_price(1_234_567.89) == "1 234 567.89"
