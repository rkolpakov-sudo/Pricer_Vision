"""Unit tests for src.pdf2spec.fullname — mother-child absorption."""
import pytest
from src.pdf2spec.fullname import resolve_mother_child, _is_short_designation, _is_continuation


class TestIsShortDesignation:
    def test_dn15(self):
        assert _is_short_designation('DN15', '')

    def test_mm(self):
        assert _is_short_designation('400 мм', '')

    def test_fraction(self):
        assert _is_short_designation('20/20', '')

    def test_long_name(self):
        assert not _is_short_designation('Труба медная для отопления', '')

    def test_empty_type_with_digits(self):
        assert _is_short_designation('', '20/15')


class TestIsContinuation:
    def test_lowercase_start(self):
        assert _is_continuation('замкнутым кожухом')

    def test_uppercase_start(self):
        assert not _is_continuation('Труба медная')

    def test_empty(self):
        assert not _is_continuation('')


class TestResolveMotherChild:
    def _make_row(self, **kwargs):
        defaults = {
            'role': 'item', 'name': '', 'type': '', 'code': '',
            'supplier': '', 'unit': '', 'qty': '', 'mass': '',
            'note': '', 'poz': '', 'page': 1,
        }
        defaults.update(kwargs)
        return defaults

    def test_mother_absorbed(self):
        mother = self._make_row(
            name='Конвектор «Универсал»', type='МКСК', qty='', role='item',
        )
        child = self._make_row(
            name='DN15', type='', qty='5', role='item',
        )
        rows, log = resolve_mother_child([mother, child], [])
        # Mother absorbed, child gets mother's name
        assert len(rows) == 1
        assert rows[0]['name'] == 'Конвектор «Универсал»'
        assert rows[0]['qty'] == '5'
        assert any(l['type'] == 'MOTHER_ABSORBED' for l in log)

    def test_child_inherits_name(self):
        mother = self._make_row(
            name='Конвектор', type='', qty='', role='item',
        )
        child = self._make_row(
            name='', type='400 мм', qty='22', role='item',
        )
        rows, log = resolve_mother_child([mother, child], [])
        assert len(rows) == 1
        assert rows[0]['name'] == 'Конвектор'
        assert rows[0]['type'] == '400 мм'

    def test_child_with_short_designation(self):
        mother = self._make_row(
            name='Кран шаровый', type='DN20', qty='', role='item',
        )
        child = self._make_row(
            name='DN15', type='', qty='3', role='item',
        )
        rows, log = resolve_mother_child([mother, child], [])
        assert len(rows) == 1
        assert rows[0]['name'] == 'Кран шаровый'
        assert 'DN15' in rows[0]['type']

    def test_header_resets_mother(self):
        header = self._make_row(name='АРМАТУРА', role='header')
        mother = self._make_row(name='Кран', qty='', role='item')
        child = self._make_row(name='DN15', qty='3', role='item')
        rows, log = resolve_mother_child([header, mother, child], [])
        assert len(rows) == 2
        assert rows[0]['role'] == 'header'
        assert rows[1]['name'] == 'Кран'
        assert rows[1]['type'] == 'DN15'

    def test_continuation_merges(self):
        mother = self._make_row(
            name='Труба', type='', qty='', role='item',
        )
        cont = self._make_row(
            name='замкнутым кожухом', type='', qty='', role='item',
        )
        rows, log = resolve_mother_child([mother, cont], [])
        assert len(rows) == 1
        assert 'замкнутым кожухом' in rows[0]['name']

    def test_all_caps_becomes_header(self):
        row = self._make_row(name='СИСТЕМА ОТОПЛЕНИЯ', role='item')
        rows, log = resolve_mother_child([row], [])
        assert rows[0]['role'] == 'header'

    def test_existing_items_unaffected(self):
        item = self._make_row(name='Труба', qty='10', unit='м', role='item')
        rows, log = resolve_mother_child([item], [])
        assert len(rows) == 1
        assert rows[0]['name'] == 'Труба'

    def test_group_inherit_after_mother(self):
        mother = self._make_row(
            name='Коллектор', type='', qty='', role='item',
        )
        child = self._make_row(
            name='', type='20/15', qty='2', role='item',
        )
        rows, log = resolve_mother_child([mother, child], [])
        assert len(rows) == 1
        assert rows[0]['name'] == 'Коллектор'
        assert rows[0]['type'] == '20/15'
