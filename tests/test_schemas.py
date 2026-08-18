import pytest
from pydantic import ValidationError

from src.models.schemas import (
    ActionType, AgentDecision, ExtractedPrice, ExtractionResult,
)


class TestActionType:
    def test_values(self):
        assert ActionType.CLICK.value == "click"
        assert ActionType.NAVIGATE.value == "navigate"
        assert ActionType.TYPE.value == "type"


class TestExtractionResult:
    def test_minimal(self):
        r = ExtractionResult(spec_text="ВВГ 3x1.5", found=False)
        assert r.product_type == "unknown"
        assert r.price is None
        assert r.requires_review is True
        assert r.confidence == 0.0

    def test_with_price(self):
        r = ExtractionResult(spec_text="x", found=True, price=1500.5,
                             confidence=0.9, url="https://tinko.ru", site="tinko.ru")
        assert r.price == 1500.5
        assert r.found is True
        assert r.site == "tinko.ru"

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError):
            ExtractionResult(spec_text="x", found=True, price=-5)

    def test_missing_spec_rejected(self):
        with pytest.raises(ValidationError):
            ExtractionResult(spec_text="", found=True)

    def test_model_dump_roundtrip(self):
        r = ExtractionResult(spec_text="Кабель", found=True, price=100.0,
                             confidence=0.95, url="u", site="s", elapsed=1.5)
        d = r.model_dump()
        assert d["spec_text"] == "Кабель"
        assert d["found"] is True
        assert d["price"] == 100.0
        assert d["elapsed"] == 1.5
        assert d["brand_mismatch"] is False

    def test_brand_mismatch_flag(self):
        r = ExtractionResult(spec_text="Клапан", found=True, price=100.0,
                             confidence=0.4, requires_review=True, brand_mismatch=True)
        assert r.brand_mismatch is True
        assert r.model_dump()["brand_mismatch"] is True


class TestExtractedPrice:
    def test_valid(self):
        p = ExtractedPrice(product_name="Кабель", price=250.5, url="https://x.ru")
        assert p.price == 250.5
        assert p.currency == "RUB"
        assert p.confidence == 0.5

    def test_price_must_be_positive(self):
        with pytest.raises(ValidationError):
            ExtractedPrice(product_name="x", price=0, url="u")

    def test_anomalous_price_rejected(self):
        with pytest.raises(ValidationError):
            ExtractedPrice(product_name="x", price=50_000_000, url="u")

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            ExtractedPrice(product_name="x", price=100, url="u", confidence=1.5)


class TestAgentDecision:
    def test_click_requires_target(self):
        with pytest.raises(ValidationError):
            AgentDecision(reasoning="нужно нажать кнопку поиска", action=ActionType.CLICK)

    def test_click_with_target_ok(self):
        d = AgentDecision(reasoning="нужно нажать кнопку поиска", action=ActionType.CLICK,
                          target="textbox")
        assert d.target == "textbox"

    def test_navigate_no_target_ok(self):
        d = AgentDecision(reasoning="перейти на сайт магазина", action=ActionType.NAVIGATE)
        assert d.target is None

    def test_reasoning_min_length(self):
        with pytest.raises(ValidationError):
            AgentDecision(reasoning="коротко", action=ActionType.NAVIGATE)

    def test_confidence_default(self):
        d = AgentDecision(reasoning="обоснование действия агента", action=ActionType.NAVIGATE)
        assert d.confidence == 0.5