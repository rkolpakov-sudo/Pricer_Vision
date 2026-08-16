import pytest

from src.adaptive_limits import (
    AdaptiveRoundManager,
    BASE_ROUNDS,
    MIN_ROUNDS,
    MAX_ROUNDS,
)


@pytest.fixture
def manager():
    return AdaptiveRoundManager()


def test_defaults(manager):
    assert manager.base_rounds == BASE_ROUNDS
    assert manager.min_rounds == MIN_ROUNDS
    assert manager.max_rounds == MAX_ROUNDS


def test_simple_site_low_rounds(manager):
    limit = manager.calculate_limit({"success_rate": 1.0, "consecutive_failures": 0})
    assert MIN_ROUNDS <= limit <= MAX_ROUNDS
    assert limit <= BASE_ROUNDS


def test_low_success_rate_raises_limit(manager):
    easy = manager.calculate_limit({"success_rate": 1.0, "consecutive_failures": 0})
    hard = manager.calculate_limit({"success_rate": 0.2, "consecutive_failures": 0})
    assert hard > easy


def test_failures_raise_limit(manager):
    fresh = manager.calculate_limit({"success_rate": 1.0, "consecutive_failures": 0})
    failed = manager.calculate_limit({"success_rate": 1.0, "consecutive_failures": 5})
    assert failed > fresh


def test_antibot_raises_limit(manager):
    plain = manager.calculate_limit({"success_rate": 0.5, "has_antibot": False})
    antibot = manager.calculate_limit({"success_rate": 0.5, "has_antibot": True})
    assert antibot > plain


def test_product_complexity_increases_limit(manager):
    base = manager.calculate_limit({"success_rate": 1.0}, product_complexity=0.0)
    complex = manager.calculate_limit({"success_rate": 1.0}, product_complexity=1.0)
    assert complex >= base


def test_limit_clamped_min(manager):
    limit = manager.calculate_limit({"success_rate": 1.0, "consecutive_failures": 0, "has_antibot": False})
    assert limit >= manager.min_rounds


def test_should_extend(manager):
    assert manager.should_extend(1, 0.5) is True
    assert manager.should_extend(1, 0.1) is False


def test_per_site_limits_respect_failures(manager):
    sites = [
        {"id": "a.ru", "consecutive_failures": 0},
        {"id": "b.ru", "consecutive_failures": 5},
    ]
    limits = manager.per_site_limits(sites)
    assert limits["a.ru"] == manager.base_rounds
    assert limits["b.ru"] == manager.min_rounds


def test_per_site_limits_empty(manager):
    assert manager.per_site_limits([]) == {}
    assert manager.per_site_limits([{"consecutive_failures": 0}]) == {}
