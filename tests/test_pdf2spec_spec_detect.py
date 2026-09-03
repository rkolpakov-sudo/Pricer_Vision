"""Unit tests for src.pdf2spec.spec_detect — column mapping, spec detection."""
import pytest
from src.pdf2spec.spec_detect import (
    map_columns, spec_score, find_spec_header, is_frame_row, detect_template, _norm_cell,
)


class TestNormCell:
    def test_soft_hyphen(self):
        assert _norm_cell('Код оборудо-\nвания') == 'Код оборудования'

    def test_mixed(self):
        assert _norm_cell('Еди-\nница изме-\nрения') == 'Единица измерения'

    def test_none(self):
        assert _norm_cell(None) == ''

    def test_hard_hyphen_preserved(self):
        result = _norm_cell('Кол-во')
        assert 'Кол-во' in result


class TestMapColumns:
    def test_vk_header(self):
        header = [
            'Поз.', 'Наименование и техническая характеристика',
            'Тип, марка, обозначение', 'Код продукции',
            'Поставщик', 'Ед. измерения', 'Кол.', 'Масса 1 ед., кг', 'Примечание',
        ]
        cols = map_columns(header)
        assert cols['poz'] == 0
        assert cols['name'] == 1
        assert cols['type'] == 2
        assert cols['code'] == 3
        assert cols['supplier'] == 4
        assert cols['unit'] == 5
        assert cols['qty'] == 6
        assert cols['mass'] == 7
        assert cols['note'] == 8

    def test_ov_header(self):
        header = [
            'Позиция', 'Наименование и техническая характеристика',
            'Тип, марка', 'Код оборудования, изделия, материала',
            'Завод-изготовитель', 'Единица измерения', 'Количество',
            'Масса единицы (кг)', 'Примечание',
        ]
        cols = map_columns(header)
        assert cols['poz'] == 0
        assert cols['supplier'] == 4

    def test_missing_columns(self):
        header = ['Поз.', 'Наименование', 'Кол.']
        cols = map_columns(header)
        assert cols['poz'] == 0
        assert cols['name'] == 1
        assert cols['qty'] == 2
        assert cols['type'] is None
        assert cols['code'] is None


class TestSpecScore:
    def test_full_header(self):
        header = [
            'Поз.', 'Наименование', 'Тип, марка', 'Код продукции',
            'Поставщик', 'Ед. измерения', 'Кол.', 'Масса', 'Примечание',
        ]
        assert spec_score(header) == 9

    def test_partial_header(self):
        header = ['№', 'Наименование', 'Кол.']
        score = spec_score(header)
        assert score < 6

    def test_empty_header(self):
        assert spec_score([]) == 0


class TestFindSpecHeader:
    def test_finds_vk_header(self):
        rows = [
            ['Поз.', 'Наименование', 'Тип, марка', 'Код продукции',
             'Поставщик', 'Ед.', 'Кол.', 'Масса', 'Примечание'],
            ['1', 'Труба', '', '', '', 'м', '10', '', ''],
        ]
        assert find_spec_header(rows) == 0

    def test_returns_none_for_non_spec(self):
        rows = [
            ['№', 'Помещение', 'Площадь'],
            ['1', 'Кабинет', '15'],
        ]
        assert find_spec_header(rows) is None

    def test_finds_ov_header(self):
        rows = [
            ['Позиция', 'Наименование', 'Тип, марка', 'Код оборудования',
             'Завод', 'Единица', 'Количество', 'Масса', 'Примечание'],
            ['1', 'Кран', '', '', '', 'шт', '5', '', ''],
        ]
        assert find_spec_header(rows) == 0


class TestIsFrameRow:
    def test_frame_row(self):
        rec = {'name': '', 'poz': '', 'note': 'Лист 08'}
        assert is_frame_row(rec) is True

    def test_normal_row(self):
        rec = {'name': 'Труба', 'poz': '1'}
        assert is_frame_row(rec) is False

    def test_stamp_row(self):
        rec = {'name': '', 'type': 'Изм. Подп.'}
        assert is_frame_row(rec) is True


class TestDetectTemplate:
    def test_ov(self):
        assert detect_template(['Позиция', 'Наименование']) == 'OV'

    def test_vk(self):
        assert detect_template(['Поз.', 'Наименование']) == 'VK'
