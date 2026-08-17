"""Тесты сессионного отрицательного кэша не найденных товаров."""

import pytest

from src.session_cache import NegativeCache


class TestNegativeCache:
    def test_not_blocked_initially(self):
        cache = NegativeCache()
        assert cache.is_blocked("Кран шаровой Ду15") is False

    def test_one_failure_not_enough(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is False
        assert cache.count("Кран шаровой Ду15") == 1

    def test_two_failures_blocks(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is True
        assert cache.count("Кран шаровой Ду15") == 2

    def test_third_occurrence_still_blocked(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is True

    def test_different_products_independent(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        cache.record("Клапан балансировочный Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is True
        assert cache.is_blocked("Клапан балансировочный Ду15") is False

    def test_normalization_case_and_spaces(self):
        cache = NegativeCache()
        cache.record("  Кран шаровой Ду15 ")
        cache.record("кран шаровой ду15")
        assert cache.is_blocked("КРАН ШАРОВОЙ    ДУ15") is True

    def test_empty_spec_never_blocks(self):
        cache = NegativeCache()
        cache.record("")
        cache.record("   ")
        assert cache.is_blocked("") is False
        assert cache.count("") == 0

    def test_custom_limit(self):
        cache = NegativeCache(limit=3)
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is False
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is True

    def test_reset(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        assert cache.is_blocked("Кран шаровой Ду15") is True
        cache.reset()
        assert cache.is_blocked("Кран шаровой Ду15") is False

    def test_blocked_count(self):
        cache = NegativeCache()
        cache.record("Кран шаровой Ду15")
        cache.record("Кран шаровой Ду15")
        cache.record("Клапан балансировочный Ду15")
        cache.record("Клапан балансировочный Ду15")
        assert cache.blocked_count() == 2

    def test_record_returns_count(self):
        cache = NegativeCache()
        assert cache.record("Кран шаровой Ду15") == 1
        assert cache.record("Кран шаровой Ду15") == 2
