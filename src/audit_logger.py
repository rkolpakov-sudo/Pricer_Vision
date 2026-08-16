import json
import uuid
from datetime import datetime
from pathlib import Path


class AuditLogger:
    def __init__(self, log_dir="data/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.uuid4())[:8]
        self.log_file = self.log_dir / f"session_{self.session_id}.jsonl"

    def log(self, event_type: str, details: dict, **extra):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            "details": details,
            **extra
        }

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_llm_request(self, messages, response, duration_ms):
        self.log("LLM_REQUEST", {
            "message_count": len(messages),
            "response_length": len(response),
            "duration_ms": duration_ms
        })

    def log_browser_action(self, action, target, result):
        self.log("BROWSER_ACTION", {
            "action": action,
            "target": target,
            "result": result
        })

    def log_extraction(self, product_name, found, price=None):
        self.log("EXTRACTION", {
            "product": product_name,
            "found": found,
            "price": price
        })

    def _read_events(self):
        if not self.log_file.exists():
            return []
        with open(self.log_file, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _calc_avg_duration(self, events):
        durations = [e["details"].get("duration_ms", 0) for e in events
                     if e.get("event_type") == "LLM_REQUEST"]
        if not durations:
            return 0.0
        return sum(durations) / len(durations)

    def get_session_summary(self):
        """Генерирует сводку по сессии для state.md"""
        events = self._read_events()
        return {
            "total_llm_calls": sum(1 for e in events if e["event_type"] == "LLM_REQUEST"),
            "total_browser_actions": sum(1 for e in events if e["event_type"] == "BROWSER_ACTION"),
            "extractions": [e for e in events if e["event_type"] == "EXTRACTION"],
            "avg_llm_duration": self._calc_avg_duration(events)
        }