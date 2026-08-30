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

    def log_extraction(self, product_name, found, price=None):
        self.log("EXTRACTION", {
            "product": product_name,
            "found": found,
            "price": price
        })

    def get_session_summary(self):
        """Генерирует сводку по сессии для state.md"""
        if not self.log_file.exists():
            return {"total_extractions": 0}
        with open(self.log_file, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        return {
            "total_extractions": sum(1 for e in events if e.get("event_type") == "EXTRACTION"),
            "found": sum(1 for e in events if e.get("event_type") == "EXTRACTION" and e.get("details", {}).get("found")),
        }
