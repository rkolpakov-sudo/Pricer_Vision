"""Unit tests for src.pdf2spec.row_classify — classification logic."""
import pytest
from src.pdf2spec.row_classify import classify, is_header, HEADER_PREFIXES


class TestIsHeader:
    def test_known_prefixes(self):
        for p in HEADER_PREFIXES:
            assert is_header(p), f"'{p}' should be a header"

    def test_system_k1(self):
        assert is_header('Хозяйственно-питьевой водопровод В1.3')

    def test_not_header(self):
        assert not is_header('Труба медная')

    def test_header_regex(self):
        assert is_header('Хозяйственно-питьевой водопровод В1.3')

    def test_too_long(self):
        long_name = 'А' * 80
        assert not is_header(long_name)


class TestClassify:
    def _make_record(self, **kwargs):
        defaults = {
            'poz': '1', 'name': '', 'type': '', 'code': '',
            'supplier': '', 'unit': '', 'qty': '', 'mass': '',
            'note': '', '_page': 1,
        }
        defaults.update(kwargs)
        return defaults

    def test_item_with_qty(self):
        rec = self._make_record(name='Труба', qty='10', unit='м')
        rows, log = classify([rec])
        assert len(rows) == 1
        assert rows[0]['role'] == 'item'
        assert rows[0]['name'] == 'Труба'
        assert rows[0]['qty'] == '10'

    def test_header(self):
        rec = self._make_record(name='Трубы и изоляция')
        rows, log = classify([rec])
        assert len(rows) == 1
        assert rows[0]['role'] == 'header'

    def test_component(self):
        rec = self._make_record(name='а) Сифон', type='ГОСТ 23289-94')
        rows, log = classify([rec])
        assert len(rows) == 1
        assert rows[0]['role'] == 'component'

    def test_continuation_merges(self):
        r1 = self._make_record(name='Труба', qty='10', unit='м', poz='1')
        r2 = self._make_record(name='Ø150х4,5', poz='')
        rows, log = classify([r1, r2])
        assert len(rows) == 1
        assert 'Ø150' in rows[0]['name']

    def test_group_inherit(self):
        r1 = self._make_record(name='Радиатор', qty='5', unit='шт', poz='1')
        r2 = self._make_record(name='', type='CC11 500x800', qty='2', poz='')
        rows, log = classify([r1, r2])
        assert len(rows) == 2
        assert rows[1]['name'] == 'Радиатор'
        assert rows[1]['role'] == 'item'

    def test_empty_name_no_type_skipped(self):
        r1 = self._make_record(name='', type='', qty='', poz='')
        rows, log = classify([r1])
        assert len(rows) == 0
        assert any(l['type'] == 'EMPTY_NAME' for l in log)

    def test_subheader_number_filtered(self):
        rec = self._make_record(name='1', poz='1')
        rows, log = classify([rec])
        assert len(rows) == 0

    def test_header_resets_prev(self):
        r1 = self._make_record(name='Трубы и изоляция')
        r2 = self._make_record(name='Ø150х4,5', poz='')
        rows, log = classify([r1, r2])
        assert len(rows) == 2
        assert rows[1]['role'] == 'item'

    def test_multiple_items(self):
        records = [
            self._make_record(name='Труба', qty='10', unit='м', poz='1'),
            self._make_record(name='Кран', qty='5', unit='шт', poz='2'),
        ]
        rows, log = classify(records)
        assert len(rows) == 2
        assert all(r['role'] == 'item' for r in rows)
