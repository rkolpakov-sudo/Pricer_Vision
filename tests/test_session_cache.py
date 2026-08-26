"""Тесты сессионного отрицательного кэша не найденных товаров и блэклиста сайтов."""

import pytest

from src.session_cache import NegativeCache, SiteBlacklist


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


class TestSiteBlacklist:
    def test_not_blocked_initially(self):
        bl = SiteBlacklist()
        assert bl.is_blocked("santech.ru") is False

    def test_one_strike_not_enough(self):
        bl = SiteBlacklist()
        assert bl.strike("santech.ru") == 1
        assert bl.is_blocked("santech.ru") is False
        assert bl.count("santech.ru") == 1

    def test_two_strikes_blocks(self):
        bl = SiteBlacklist()
        bl.strike("santech.ru")
        bl.strike("santech.ru")
        assert bl.is_blocked("santech.ru") is True
        assert "santech.ru" in bl.blocked_sites()

    def test_different_sites_independent(self):
        bl = SiteBlacklist()
        bl.strike("santech.ru")
        bl.strike("santech.ru")
        bl.strike("mircli.ru")
        assert bl.is_blocked("santech.ru") is True
        assert bl.is_blocked("mircli.ru") is False

    def test_normalization_trailing_slash_case(self):
        bl = SiteBlacklist()
        bl.strike("https://www.Santech.ru/")
        bl.strike("santech.ru")
        assert bl.is_blocked("www.santech.ru") is True

    def test_empty_site_never_blocks(self):
        bl = SiteBlacklist()
        bl.strike("")
        assert bl.is_blocked("") is False
        assert bl.count("") == 0

    def test_custom_limit(self):
        bl = SiteBlacklist(limit=3)
        bl.strike("santech.ru")
        bl.strike("santech.ru")
        assert bl.is_blocked("santech.ru") is False
        bl.strike("santech.ru")
        assert bl.is_blocked("santech.ru") is True

    def test_reset(self):
        bl = SiteBlacklist()
        bl.strike("santech.ru")
        bl.strike("santech.ru")
        assert bl.is_blocked("santech.ru") is True
        bl.reset()
        assert bl.is_blocked("santech.ru") is False
        assert len(bl) == 0

    def test_limit_property(self):
        assert SiteBlacklist().limit == 2
        assert SiteBlacklist(limit=5).limit == 5
