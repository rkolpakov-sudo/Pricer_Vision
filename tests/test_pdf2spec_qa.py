"""Unit tests for src.pdf2spec.qa — QA scanner."""
import json
import tempfile
from pathlib import Path

import pytest
from src.pdf2spec.qa import qa, qa_scan_csv


class TestQa:
    def _make_row(self, **kwargs):
        defaults = {
            'role': 'item', 'name': 'Test', 'type': '', 'code': '',
            'supplier': '', 'unit': '', 'qty': '1', 'mass': '',
            'note': '', 'poz': '1', 'page': 1,
        }
        defaults.update(kwargs)
        return defaults

    def test_clean_output(self):
        rows = [
            self._make_row(name='Труба', qty='10'),
            self._make_row(name='Кран DN15', qty='5'),
        ]
        issues = qa(rows, [])
        assert issues['total_rows'] == 2
        assert issues['role_counts']['item'] == 2
        assert len(issues['orphans']) == 0
        assert len(issues['word_splits']) == 0

    def test_orphan_detection(self):
        log = [{'type': 'ORPHAN', 'frag': 'test'}]
        issues = qa([], log)
        assert len(issues['orphans']) == 1

    def test_empty_name_detection(self):
        log = [{'type': 'EMPTY_NAME', 'page': 1}]
        issues = qa([], log)
        assert len(issues['orphans']) == 1

    def test_word_split_detection(self):
        rows = [self._make_row(name='Труб а медная')]
        issues = qa(rows, [])
        assert 'Труб а' in issues['word_splits']

    def test_naked_diameter(self):
        rows = [self._make_row(name='Ø мм')]
        issues = qa(rows, [])
        assert len(issues['naked_diam']) == 1

    def test_items_no_qty(self):
        rows = [self._make_row(qty='', role='item')]
        issues = qa(rows, [])
        assert len(issues['items_no_qty']) == 1

    def test_role_counts(self):
        rows = [
            self._make_row(role='item', qty='1'),
            self._make_row(role='header', name='Section'),
            self._make_row(role='component', name='Part'),
        ]
        issues = qa(rows, [])
        assert issues['role_counts'] == {'item': 1, 'header': 1, 'component': 1}


class TestQaScanCsv:
    def test_clean_csv(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False, encoding='utf-8-sig') as f:
            f.write('col0;Наименование;col2\n')
            f.write('1;Труба;10\n')
            f.write('2;Кран;5\n')
            path = f.name

        header, issues = qa_scan_csv(path)
        assert len(issues) == 0
        Path(path).unlink()

    def test_double_space(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False, encoding='utf-8-sig') as f:
            f.write('col0;Наименование\n')
            f.write('1;Труба  медная\n')
            path = f.name

        header, issues = qa_scan_csv(path)
        assert any(i[1] == 'double_space' for i in issues)
        Path(path).unlink()

    def test_empty_name(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False, encoding='utf-8-sig') as f:
            f.write('col0;Наименование\n')
            f.write('1;\n')
            path = f.name

        header, issues = qa_scan_csv(path)
        assert any(i[1] == 'empty_name' for i in issues)
        Path(path).unlink()
