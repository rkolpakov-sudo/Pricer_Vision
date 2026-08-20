import pytest

from gui.metrics_panel import METRIC_DEFS, DEFAULT_STATS, format_metric_value
from src.mcp_agent_runner import _build_metrics


class TestFormatMetricValue:
    def test_none_placeholder(self):
        assert format_metric_value("processed", None) == "—"

    def test_int(self):
        assert format_metric_value("processed", 5) == "5"

    def test_integer_float(self):
        assert format_metric_value("found", 3.0) == "3"

    def test_fraction_float(self):
        assert format_metric_value("processed", 3.5) == "3.50"

    def test_success_rate(self):
        assert format_metric_value("success_rate", 0.5) == "50%"

    def test_avg_llm_time(self):
        assert format_metric_value("avg_llm_time", 2.26) == "2.3s"

    def test_tokens(self):
        assert format_metric_value("prompt_tokens", 12345) == "12 345"
        assert format_metric_value("completion_tokens", 900) == "900"


class TestBuildMetrics:
    def test_empty(self):
        m = _build_metrics(10, 0, 0, [], 0, 0, 0)
        assert m["total_products"] == 10
        assert m["processed"] == 0
        assert m["found"] == 0
        assert m["success_rate"] == 0.0
        assert m["llm_calls"] == 0
        assert m["avg_llm_time"] == 0.0
        assert m["prompt_tokens"] == 0
        assert m["completion_tokens"] == 0
        assert m["cache_hits"] == 0
        assert m["stuck_events"] == 0
        assert m["blocks"] == 0

    def test_populated(self):
        m = _build_metrics(10, 4, 3, [1.0, 2.0], 2, 1, 1, prompt_tokens=1500, completion_tokens=450)
        assert m["success_rate"] == 0.75
        assert m["llm_calls"] == 2
        assert m["avg_llm_time"] == 1.5
        assert m["prompt_tokens"] == 1500
        assert m["completion_tokens"] == 450
        assert m["cache_hits"] == 2
        assert m["stuck_events"] == 1
        assert m["blocks"] == 1

    def test_metric_keys_match_panel_defs(self):
        keys = {k for k, _ in METRIC_DEFS}
        m = _build_metrics(10, 4, 3, [1.0], 0, 0, 0)
        assert set(m.keys()) == keys

    def test_default_stats_cover_all_defs(self):
        assert set(DEFAULT_STATS.keys()) == {k for k, _ in METRIC_DEFS}
