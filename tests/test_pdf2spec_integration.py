"""Integration test: full pdf2spec pipeline on reference PDF."""
import json
from pathlib import Path

import pytest
from src.pdf2spec.orchestrator import run_deterministic
from src.pdf2spec.export_xlsx import export_xlsx
from src.pdf2spec.qa import qa


PDF_PATH = str(next(Path('.').glob('*.pdf'))) if list(Path('.').glob('*.pdf')) else None


@pytest.mark.skipif(PDF_PATH is None, reason="No PDF in project root")
class TestPipelineIntegration:
    def test_full_pipeline(self):
        result = run_deterministic(PDF_PATH)
        rows = result['rows']
        issues = result['issues']

        assert len(rows) > 100, f"Expected >100 rows, got {len(rows)}"
        assert issues['total_rows'] == len(rows)
        assert issues['role_counts'].get('item', 0) > 100

    def test_no_word_splits(self):
        result = run_deterministic(PDF_PATH)
        assert len(result['issues']['word_splits']) == 0

    def test_no_naked_diameter(self):
        result = run_deterministic(PDF_PATH)
        assert len(result['issues']['naked_diam']) == 0

    def test_xlsx_export(self):
        result = run_deterministic(PDF_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.xlsx'
            xlsx_path = export_xlsx(result['rows'], path, result.get('template', 'OV'))
            assert xlsx_path.exists()
            assert xlsx_path.stat().st_size > 1000

    def test_log_json(self):
        result = run_deterministic(PDF_PATH)
        log = result['log']
        assert isinstance(log, list)
        assert len(log) > 0

        types = {l['type'] for l in log}
        assert 'ITEM' in types
        assert 'MERGE' in types

    def test_roles_complete(self):
        result = run_deterministic(PDF_PATH)
        roles = result['issues']['role_counts']
        assert 'item' in roles
        assert 'component' in roles
        assert roles['item'] > 100
        assert roles['component'] >= 15


import tempfile
