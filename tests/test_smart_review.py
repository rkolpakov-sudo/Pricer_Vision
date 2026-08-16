"""Тесты SmartReview — выделены из test_pdf_parser в отдельный файл (Фаза 7)."""

import pytest

from src.pdf_parser.review import SmartReview


def make_row(**over):
    row = {"pos": 1, "name": "", "specs": "", "code": "", "manufacturer": "",
           "qty": 0, "unit": "", "weight": 0}
    row.update(over)
    return row


class TestSmartReviewConfidence:
    def test_full_item_max_confidence(self):
        row = make_row(name="Кабель ВВГ", specs="3х2.5", code="A1",
                       manufacturer="ООО", qty=100, unit="м")
        assert SmartReview()._calculate_confidence(row) == 1.0

    def test_name_only(self):
        assert SmartReview()._calculate_confidence(make_row(name="Труба")) == pytest.approx(0.4)

    def test_qty_only(self):
        assert SmartReview()._calculate_confidence(make_row(qty=5)) == pytest.approx(0.2)

    def test_code_and_manufacturer_mutually_exclusive(self):
        # code ИЛИ manufacturer дают +0.2, но не +0.4
        code = SmartReview()._calculate_confidence(make_row(name="X", code="C"))
        mfg = SmartReview()._calculate_confidence(make_row(name="X", manufacturer="M"))
        both = SmartReview()._calculate_confidence(make_row(name="X", code="C", manufacturer="M"))
        assert code == mfg == both == pytest.approx(0.6)

    def test_specs_adds(self):
        assert SmartReview()._calculate_confidence(make_row(name="X", specs="32")) == pytest.approx(0.5)

    def test_numeric_string_qty_counted(self):
        # qty строкой "10" — числовая строка, считается как количество
        assert SmartReview()._calculate_confidence(make_row(name="X", qty="10")) == pytest.approx(0.6)

    def test_invalid_string_qty_ignored(self):
        assert SmartReview()._calculate_confidence(make_row(name="X", qty="abc")) == pytest.approx(0.4)

    def test_negative_qty_ignored(self):
        assert SmartReview()._calculate_confidence(make_row(name="X", qty=-1)) == pytest.approx(0.4)

    def test_cap_at_one(self):
        row = make_row(name="X", specs="s", code="c", manufacturer="m", qty=1, unit="u")
        assert SmartReview()._calculate_confidence(row) == 1.0


class TestSmartReviewProcess:
    def test_threshold_boundary_auto_approved(self):
        # ровно порог 0.8 → auto-approved
        items = [make_row(name="X", code="C", qty=5, unit="u")]  # 0.4+0.2+0.2+0.1=0.9
        review = SmartReview(threshold=0.8)
        auto, needs = review.process_extraction(items)
        assert len(auto) == 1
        assert len(needs) == 0

    def test_reset_between_calls(self):
        review = SmartReview()
        items_high = [make_row(name="X", qty=1, unit="u", code="c")]
        items_low = [make_row()]
        review.process_extraction(items_high)
        auto, needs = review.process_extraction(items_low)
        assert len(auto) == 0
        assert len(needs) == 1

    def test_empty_input(self):
        review = SmartReview()
        auto, needs = review.process_extraction([])
        assert auto == []
        assert needs == []

    def test_confidence_added_to_row(self):
        items = [make_row(name="X")]
        review = SmartReview()
        review.process_extraction(items)
        assert items[0]["confidence"] == pytest.approx(0.4)
