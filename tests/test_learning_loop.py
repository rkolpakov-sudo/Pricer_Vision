import json
import pytest
from datetime import datetime, timedelta

from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.learning_loop import LearningLoop


@pytest.fixture
def mm(graph_engine):
    return MemoryManager(graph_engine)


@pytest.fixture
def learning(graph_engine, mm, tmp_path):
    profiles_path = tmp_path / "site_profiles.json"
    return LearningLoop(graph_engine, mm, site_profiles_path=str(profiles_path))


def _result(spec_text="Кабель ВВГнг 3x1.5", price=1500.0, site="tinko.ru",
            product_type="cables", elapsed=90.0, reason=""):
    return {
        "spec_text": spec_text,
        "product_type": product_type,
        "price": price,
        "site": site,
        "reason": reason,
        "requires_review": price is None,
        "elapsed": elapsed,
    }


class TestLearningLoop:
    def test_empty_results(self, learning):
        summary = learning.consolidate_after_run([])
        assert summary == {"approaches_updated": 0, "new_patterns": 0, "new_hints": 0}

    def test_generates_hint_for_long_search(self, learning):
        learning.consolidate_after_run([_result(elapsed=120.0)])
        hints = learning.hints.get_active_hints("cables", "tinko.ru")
        assert len(hints) == 1
        assert "найден после долгого поиска" in hints[0]["hint_text"]

    def test_no_hint_for_short_search(self, learning):
        learning.consolidate_after_run([_result(elapsed=30.0)])
        assert learning.hints.get_active_hints("cables", "tinko.ru") == []

    def test_no_hint_for_failed_search(self, learning):
        learning.consolidate_after_run([_result(price=None, elapsed=120.0)])
        assert learning.hints.get_active_hints("cables", "tinko.ru") == []

    def test_hint_not_duplicated(self, learning):
        learning.consolidate_after_run([_result(elapsed=120.0)])
        learning.consolidate_after_run([_result(elapsed=130.0)])
        assert len(learning.hints.get_active_hints("cables", "tinko.ru")) == 1

    def test_site_profiles_aggregated(self, learning):
        learning.consolidate_after_run([
            _result(price=100.0, elapsed=10.0),
            _result(price=None, elapsed=20.0),
            _result(price=200.0, elapsed=30.0, site="keaz.ru"),
        ])
        prof = learning.site_profiles["tinko.ru"]
        assert prof["total_runs"] == 1
        assert prof["success_rate"] == 0.5
        assert prof["avg_attempts"] == 15.0
        assert learning.site_profiles["keaz.ru"]["success_rate"] == 1.0

    def test_site_profiles_persisted(self, learning, tmp_path):
        learning.consolidate_after_run([_result(price=100.0)])
        data = json.loads((tmp_path / "site_profiles.json").read_text(encoding="utf-8"))
        assert data["tinko.ru"]["success_rate"] == 1.0

    def test_profiles_loaded_from_disk(self, learning, tmp_path):
        learning.consolidate_after_run([_result(price=100.0)])
        reloaded = LearningLoop(learning.graph, learning.memory,
                                site_profiles_path=str(tmp_path / "site_profiles.json"))
        assert reloaded.site_profiles["tinko.ru"]["success_rate"] == 1.0

    def test_block_count_from_captcha(self, learning):
        learning.consolidate_after_run([_result(price=None, reason="captcha detected")])
        assert learning.site_profiles["tinko.ru"]["block_count"] == 1

    def test_run_statistics(self, learning):
        learning.consolidate_after_run([_result(price=100.0), _result(price=None)])
        assert learning.last_run_stats["total"] == 2
        assert learning.last_run_stats["found"] == 1
        assert learning.last_run_stats["success_rate"] == 0.5

    def test_extract_patterns_noop_without_selectors(self, learning):
        summary = learning.consolidate_after_run([_result(price=100.0)])
        assert summary["new_patterns"] == 0

    def test_extract_patterns_with_selectors(self, learning):
        r = _result(price=100.0)
        r["selectors"] = {"price": {".price": True}}
        summary = learning.consolidate_after_run([r])
        assert summary["new_patterns"] == 1
        approaches = learning.memory.get_approaches_by_site("tinko.ru")
        assert any(a.get("selectors_cache") for a in approaches)
