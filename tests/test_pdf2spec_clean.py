"""Unit tests for src.pdf2spec.clean — Ø-corruption, word-splits, ОВ-фитинги."""
import pytest
from src.pdf2spec.clean import (
    clean_text, clean_name, is_diam, add_ov_steel,
    DIAMETERS, SPLITS,
)


class TestIsDiam:
    def test_known_diameters(self):
        for d in [15, 20, 25, 32, 40, 50, 65, 80, 100, 110, 125, 150, 160, 200, 219]:
            assert is_diam(str(d)), f"{d} should be a diameter"

    def test_unknown_diameter(self):
        assert not is_diam("999")
        assert not is_diam("abc")
        assert not is_diam("")


class TestCleanText:
    def test_empty(self):
        assert clean_text('') == ''
        assert clean_text(None) == ''

    def test_gost_tu_glue(self):
        assert 'ТУ-2248' in clean_text('ТУ -2248')
        assert 'ГОСТ-3262' in clean_text('ГОСТ -3262')

    def test_number_dash_glue(self):
        assert '2248-043' in clean_text('2248- 043')
        assert '2248-043' in clean_text('2248 -043')

    def test_space_before_comma(self):
        assert clean_text('6, 0') == '6,0'

    def test_space_before_dot(self):
        assert 'В1.3' in clean_text('В1 .3')

    def test_supplier_fix(self):
        assert 'Ekoplastik' in clean_text('Ekopl astik')
        assert 'Агпайп' in clean_text('Агпа йп')

    def test_split_words(self):
        for bad, good in SPLITS[:5]:
            result = clean_text(bad)
            assert ' ' not in result or result == good, \
                f"split '{bad}' should produce '{good}', got '{result}'"


class TestCleanName:
    def test_empty(self):
        assert clean_name('') == ''
        assert clean_name(None) == ''

    def test_leading_dash_removed(self):
        result = clean_name('- кран шаровой Ду20')
        assert not result.startswith('-')

    def test_diameter_6_prefix(self):
        result = clean_name('6 150х4,5')
        assert result.startswith('Ø'), f"Expected Ø prefix, got: {result}"

    def test_diameter_6_compact(self):
        result = clean_name('6150х4,5')
        assert 'Ø150' in result, f"Expected Ø150, got: {result}"

    def test_diameter_phФ(self):
        result = clean_name('ф110х400мм')
        assert 'Ø110' in result, f"Expected Ø110, got: {result}"

    def test_real_digit_not_corrupted(self):
        result = clean_name('DN160')
        assert 'DN160' in result
        assert 'Ø' not in result

    def test_du65_not_corrupted(self):
        result = clean_name('Ду=65')
        assert 'Ду=65' in result
        assert 'Ø' not in result

    def test_650x400_not_corrupted(self):
        result = clean_name('650x400')
        assert '650' in result
        assert 'Ø' not in result

    def test_x_space_between_digits(self):
        result = clean_name('150х 4,5')
        assert '150х4,5' in result

    def test_du_equal_no_space(self):
        result = clean_name('Д =110')
        assert 'Д=110' in result


class TestAddOvSteel:
    def test_otvod(self):
        assert add_ov_steel('Отвод-45') == 'Отвод-45 стальной'

    def test_troynik(self):
        assert add_ov_steel('Тройник-90') == 'Тройник-90 стальной'

    def test_perehod(self):
        assert add_ov_steel('Переход') == 'Переход стальной'

    def test_mufta(self):
        assert add_ov_steel('Муфта') == 'Муфта стальная'

    def test_zaglushka(self):
        result = add_ov_steel('Заглушка 250x250')
        assert 'стальн' in result

    def test_already_has_steel(self):
        assert add_ov_steel('Отвод стальной') == 'Отвод стальной'

    def test_unrecognized_passthrough(self):
        assert add_ov_steel('Труба медная') == 'Труба медная'

    def test_empty(self):
        assert add_ov_steel('') == ''
