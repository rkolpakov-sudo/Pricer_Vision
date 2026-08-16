import json

from src.audit_logger import AuditLogger


class TestAuditLogger:
    def test_creates_log_dir(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path / "audit"))
        assert (tmp_path / "audit").is_dir()
        assert log.log_file.exists() is False  # lazy: file created on first log

    def test_log_writes_jsonl(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log("TEST_EVENT", {"key": "value"})
        lines = log.log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "TEST_EVENT"
        assert entry["details"]["key"] == "value"
        assert entry["session_id"] == log.session_id
        assert "timestamp" in entry

    def test_log_llm_request(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_llm_request([{"role": "user", "content": "hi"}], "response text", 150)
        events = log._read_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "LLM_REQUEST"
        assert events[0]["details"]["message_count"] == 1
        assert events[0]["details"]["response_length"] == len("response text")
        assert events[0]["details"]["duration_ms"] == 150

    def test_log_browser_action(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_browser_action("browser_navigate", "https://x.ru", "ok")
        events = log._read_events()
        assert events[0]["event_type"] == "BROWSER_ACTION"
        assert events[0]["details"]["action"] == "browser_navigate"

    def test_log_extraction(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_extraction("Кабель", True, 1500.5)
        events = log._read_events()
        assert events[0]["event_type"] == "EXTRACTION"
        assert events[0]["details"]["found"] is True
        assert events[0]["details"]["price"] == 1500.5

    def test_get_session_summary(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_llm_request([], "a", 100)
        log.log_llm_request([], "bb", 300)
        log.log_browser_action("click", "x", "ok")
        log.log_extraction("Кабель", True, 500)
        summary = log.get_session_summary()
        assert summary["total_llm_calls"] == 2
        assert summary["total_browser_actions"] == 1
        assert len(summary["extractions"]) == 1
        assert summary["extractions"][0]["details"]["price"] == 500
        assert summary["avg_llm_duration"] == 200.0

    def test_get_session_summary_empty(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        summary = log.get_session_summary()
        assert summary["total_llm_calls"] == 0
        assert summary["total_browser_actions"] == 0
        assert summary["extractions"] == []
        assert summary["avg_llm_duration"] == 0.0