from src.stuck_detector import StuckDetector, StuckLevel


class TestStuckDetector:
    def test_ok_with_few_actions(self):
        d = StuckDetector()
        d.record_action("browser_navigate", "https://x.ru", "success")
        assert d.detect() == StuckLevel.OK

    def test_ok_with_varied_actions(self):
        d = StuckDetector()
        for a, t, r in [
            ("navigate", "https://x.ru", "success"),
            ("click", "button", "success"),
            ("type", "textbox", "success"),
            ("click", "link", "success"),
        ]:
            d.record_action(a, t, r)
        assert d.detect() == StuckLevel.OK

    def test_critical_on_repeat(self):
        d = StuckDetector(repeat_threshold=3)
        for _ in range(3):
            d.record_action("browser_click", "e123", "success")
        assert d.detect() == StuckLevel.CRITICAL

    def test_warning_on_no_progress(self):
        d = StuckDetector(repeat_threshold=3)
        for t in ("tab1", "tab2", "tab3"):
            d.record_action("browser_snapshot", t, "no_change")
        assert d.detect() == StuckLevel.WARNING

    def test_blocked_on_captcha(self):
        d = StuckDetector()
        for _ in range(3):
            d.record_action("browser_snapshot", "", "captcha blocked")
        assert d.detect() == StuckLevel.BLOCKED

    def test_blocked_on_cloudflare(self):
        d = StuckDetector()
        d.record_action("navigate", "https://x.ru", "success")
        d.record_action("navigate", "https://x.ru", "access denied 403")
        d.record_action("navigate", "https://x.ru", "access denied 403")
        assert d.detect() == StuckLevel.BLOCKED

    def test_repeat_threshold_boundary(self):
        d = StuckDetector(repeat_threshold=4)
        for _ in range(3):
            d.record_action("click", "x", "success")
        assert d.detect() == StuckLevel.OK
        d.record_action("click", "x", "success")
        assert d.detect() == StuckLevel.CRITICAL

    def test_success_resets_no_progress(self):
        d = StuckDetector(repeat_threshold=3)
        d.record_action("click", "a", "no_change")
        d.record_action("click", "b", "no_change")
        d.record_action("click", "c", "success")
        assert d.detect() == StuckLevel.OK

    def test_reset(self):
        d = StuckDetector(repeat_threshold=3)
        for _ in range(3):
            d.record_action("click", "x", "success")
        assert d.detect() == StuckLevel.CRITICAL
        d.reset()
        assert d.detect() == StuckLevel.OK
        assert len(d.history) == 0

    def test_window_size_limits_history(self):
        d = StuckDetector(window_size=2, repeat_threshold=3)
        for _ in range(5):
            d.record_action("click", "x", "success")
        # history bounded to window_size
        assert len(d.history) <= 2

    def test_suggest_recovery_ok_empty(self):
        d = StuckDetector()
        assert d.suggest_recovery(StuckLevel.OK) == []

    def test_suggest_recovery_critical(self):
        d = StuckDetector()
        assert "SWITCH_SITE" in d.suggest_recovery(StuckLevel.CRITICAL)

    def test_suggest_recovery_blocked(self):
        d = StuckDetector()
        rec = d.suggest_recovery(StuckLevel.BLOCKED)
        assert "SWITCH_SITE" in rec
        assert "WAIT_AND_RETRY" in rec