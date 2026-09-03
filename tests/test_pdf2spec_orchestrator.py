"""Unit tests for src.pdf2spec.orchestrator — deterministic + LLM review."""
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.pdf2spec.orchestrator import (
    run_deterministic, _build_review_prompt, _apply_fixes, _load_rules, _save_rules,
)


PDF_PATH = str(next(Path(".").glob("*.pdf"))) if list(Path(".").glob("*.pdf")) else None


class TestBuildReviewPrompt:
    def _make_result(self, orphans=None, items_no_qty=None, word_splits=None):
        issues = {
            "total_rows": 10,
            "role_counts": {"item": 8, "header": 1, "component": 1},
            "orphans": orphans or [],
            "items_no_qty": items_no_qty or [],
            "word_splits": word_splits or [],
        }
        return {"rows": [], "log": [], "issues": issues}

    def test_basic_prompt(self):
        result = self._make_result()
        prompt = _build_review_prompt(result)
        assert "эксперт по спецификациям" in prompt
        assert "10" in prompt

    def test_with_orphans(self):
        log = [{"type": "ORPHAN", "page": 5, "name": "Труба"}]
        result = self._make_result()
        result["log"] = log
        prompt = _build_review_prompt(result)
        assert "ORPHAN" in prompt
        assert "Труба" in prompt

    def test_with_items_no_qty(self):
        result = self._make_result(items_no_qty=["Кран шаровый"])
        prompt = _build_review_prompt(result)
        assert "Кран шаровый" in prompt

    def test_json_format_in_prompt(self):
        result = self._make_result()
        prompt = _build_review_prompt(result)
        assert "fixes" in prompt
        assert "new_splits" in prompt
        assert "new_headers" in prompt


class TestApplyFixes:
    def _make_result(self):
        rows = [
            {"role": "item", "name": "Труба", "qty": "10", "page": 1},
            {"role": "item", "name": "Кран", "qty": "5", "page": 2},
        ]
        log = []
        issues = {"total_rows": 2, "role_counts": {"item": 2}}
        return {"rows": rows, "log": log, "issues": issues}

    def test_fix_role(self):
        result = self._make_result()
        review = {
            "fixes": [
                {"page": 2, "name": "Кран", "role": "header", "reason": "test"}
            ]
        }
        result = _apply_fixes(result, review)
        assert result["rows"][1]["role"] == "header"
        assert any(l["type"] == "LLM_FIX" for l in result["log"])

    def test_fix_ignore(self):
        result = self._make_result()
        review = {"fixes": [{"page": 1, "name": "Труба", "role": "ignore"}]}
        result = _apply_fixes(result, review)
        assert result["rows"][0]["role"] == "item"

    def test_new_splits(self):
        result = self._make_result()
        review = {"new_splits": [["тест а", "теста"]]}
        result = _apply_fixes(result, review)
        from src.pdf2spec.clean import SPLITS
        assert ("тест а", "теста") in SPLITS

    def test_new_headers(self):
        result = self._make_result()
        review = {"new_headers": ["Новый раздел"]}
        result = _apply_fixes(result, review)
        from src.pdf2spec.row_classify import HEADER_PREFIXES
        assert "Новый раздел" in HEADER_PREFIXES


class TestRulesPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            import src.pdf2spec.orchestrator as orch
            original_dir = orch.RULES_DIR
            orch.RULES_DIR = Path(tmp) / "rules"
            try:
                rules = {"splits": [["a", "b"]], "headers": ["Test"], "diameters": []}
                _save_rules(rules)
                loaded = _load_rules()
                assert loaded["splits"] == [["a", "b"]]
                assert loaded["headers"] == ["Test"]
            finally:
                orch.RULES_DIR = original_dir


class TestLlmReviewMock:
    def test_review_applies_fixes(self):
        rows = [
            {"role": "item", "name": "Труба", "qty": "10", "page": 1},
        ]
        result = {
            "rows": rows,
            "log": [],
            "issues": {
                "total_rows": 1,
                "role_counts": {"item": 1},
                "orphans": [],
                "items_no_qty": [],
                "word_splits": [],
            },
        }

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "fixes": [{"page": 1, "name": "Труба", "role": "header", "reason": "test"}],
                        "new_splits": [],
                        "new_headers": [],
                    })
                }
            }]
        }

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=mock_response)

        from src.pdf2spec.orchestrator import llm_review
        import asyncio
        result = asyncio.run(llm_review(result, mock_client))

        assert result["rows"][0]["role"] == "header"
        mock_client.chat.assert_called_once()


@pytest.mark.skipif(PDF_PATH is None, reason="No PDF in project root")
class TestDeterministicIntegration:
    def test_full_pipeline(self):
        result = run_deterministic(PDF_PATH)
        rows = result["rows"]
        assert len(rows) > 100
        assert result["issues"]["total_rows"] == len(rows)

    def test_roles_complete(self):
        result = run_deterministic(PDF_PATH)
        roles = result["issues"]["role_counts"]
        assert roles["item"] > 100
        assert roles["component"] >= 15
