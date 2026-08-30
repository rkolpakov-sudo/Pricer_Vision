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

    def test_log_extraction(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_extraction("Кабель", True, 1500.5)
        lines = log.log_file.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["event_type"] == "EXTRACTION"
        assert entry["details"]["found"] is True
        assert entry["details"]["price"] == 1500.5

    def test_session_id_unique(self, tmp_path):
        log1 = AuditLogger(log_dir=str(tmp_path))
        log2 = AuditLogger(log_dir=str(tmp_path))
        assert log1.session_id != log2.session_id

    def test_get_session_summary(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        log.log_extraction("Кабель", True, 500)
        log.log_extraction("Труба", False, None)
        log.log_extraction("Фитинг", True, 120)
        summary = log.get_session_summary()
        assert summary["total_extractions"] == 3
        assert summary["found"] == 2

    def test_get_session_summary_empty(self, tmp_path):
        log = AuditLogger(log_dir=str(tmp_path))
        summary = log.get_session_summary()
        assert summary["total_extractions"] == 0
