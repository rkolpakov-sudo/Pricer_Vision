import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("pricer.pdf.feedback")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pdf_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    correction_type TEXT NOT NULL DEFAULT 'manual',
    apply_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_pdf_corrections_original
    ON pdf_corrections(original_text);
"""


class FeedbackCollector:
    def __init__(self, db_path: str = "data/pricer.db"):
        self._db_path = str(db_path)
        self._ensure_table()

    def _ensure_table(self):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(CREATE_TABLE_SQL)
                conn.execute(CREATE_INDEX_SQL)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to create pdf_corrections table: {e}")

    def save_correction(self, original_text: str, corrected_text: str,
                        correction_type: str = "manual") -> bool:
        if original_text == corrected_text:
            return False
        try:
            with sqlite3.connect(self._db_path) as conn:
                existing = conn.execute(
                    "SELECT id, apply_count FROM pdf_corrections WHERE original_text = ?",
                    (original_text,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE pdf_corrections SET corrected_text = ?, apply_count = apply_count + 1, updated_at = datetime('now') WHERE id = ?",
                        (corrected_text, existing[0])
                    )
                else:
                    conn.execute(
                        "INSERT INTO pdf_corrections (original_text, corrected_text, correction_type, apply_count) VALUES (?, ?, ?, 1)",
                        (original_text, corrected_text, correction_type)
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save correction: {e}")
            return False

    def get_correction(self, original_text: str) -> str | None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT corrected_text FROM pdf_corrections WHERE original_text = ? ORDER BY updated_at DESC LIMIT 1",
                    (original_text,)
                ).fetchone()
                if row:
                    return row[0]
        except Exception as e:
            logger.error(f"Failed to get correction: {e}")
        return None

    def apply_corrections(self, items: list[dict]) -> list[dict]:
        corrected = []
        for item in items:
            original_name = item.get("name", "")
            corrected_name = self.get_correction(original_name)
            if corrected_name:
                item["name"] = corrected_name
                item["_corrected"] = True
            corrected.append(item)
        return corrected

    def get_stats(self) -> dict:
        try:
            with sqlite3.connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM pdf_corrections").fetchone()[0]
                total_applies = conn.execute("SELECT COALESCE(SUM(apply_count), 0) FROM pdf_corrections").fetchone()[0]
                return {"total_corrections": total, "total_applies": total_applies}
        except Exception:
            return {"total_corrections": 0, "total_applies": 0}
