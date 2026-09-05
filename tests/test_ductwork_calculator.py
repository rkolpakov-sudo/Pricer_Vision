"""Тесты модуля расчёта воздуховодов и фасонных частей (src/ductwork_calculator.py).

Модель расчёта (портирована из Proj_duct, подтверждена result-файлами):
- цена за изделие = S_м.п. × 1.05 (припуск) × цена_м² × K_толщины × L_ном;
- L_ном: круглый воздуховод 3000 мм, прямоугольный 1250 мм;
- фасонные части — в шт, L_ном не применяется;
- отвод прямоугольный: R = 1.0×max(A,B).
"""

import math

from src.ductwork_calculator import (
    apply_ocr_fixes, fix_circle_notation, normalize_diameter_symbols,
    detect_element_type,
    is_ductwork_row, count_ductwork_items, calculate_ductwork_row,
    calc_area, NOMENCLATURE_LENGTHS, infer_spec_context,
)


class TestOcr:
    def test_thickness_fixes(self):
        assert "δ=0,8мм" in apply_ocr_fixes("300x300, 6=0,8мм")
        assert "δ=0,8мм" in apply_ocr_fixes("300x300, 5=0,8мм")
        assert "δ=0,8мм" in apply_ocr_fixes("300x300, б=0,8мм")

    def test_circle_notation(self):
        assert fix_circle_notation("Воздуховод 0100") == "Воздуховод Ø100"
        assert fix_circle_notation("⌀200") == "Ø200"
        assert fix_circle_notation("ø160") == "Ø160"
        assert fix_circle_notation("ф20") == "Ø20"

    def test_cyrillic_o_to_zero(self):
        assert "0" in apply_ocr_fixes("О100")


class TestDetectElementType:
    def test_duct_straight(self):
        assert detect_element_type("Воздуховод из оцинкованной стали 300x300") == "duct_straight"

    def test_elbow(self):
        assert detect_element_type("Отвод круглого воздуховода 90° Ø200") == "elbow_round"
        assert detect_element_type("Отвод прямоугольного воздуховода 20° 400x250") == "elbow_rect"

    def test_transitions(self):
        assert detect_element_type("Переход круглого сечения Ø200/Ø160") == "transition_round"
        assert detect_element_type("Переход прямоугольного сечения 300x200-250x200") == "transition_rect"
        assert detect_element_type("Переход воздуховода с круглого на прямоугольное сечение 200x200-Ø200") == "transition_mix"

    def test_caps_taps_hoods(self):
        assert detect_element_type("Заглушка круглая Ø100") == "cap_round"
        assert detect_element_type("Заглушка прямоугольная 400x600") == "cap_rect"
        assert detect_element_type("Врезка прямоугольная 300x300") == "tap_rect"
        assert detect_element_type("Зонт пристенный 400x300") == "hood_wall"
        assert detect_element_type("Дефлектор Ø200") == "deflector"
        assert detect_element_type("Утка круглая Ø100") == "offset_round"

    def test_tees_flex(self):
        assert detect_element_type("Тройник круглого воздуховода Ø200") == "tee_round"
        assert detect_element_type("Вставка гибкая WG 70-40") == "flex_insert"

    def test_other(self):
        assert detect_element_type("Вентилятор канальный VC-160") == "other"
        assert detect_element_type("Кран шаровой Ду15") == "other"
        assert detect_element_type("Воздушный клапан круглого сечения: Ø200") == "other"


class TestArea:
    def test_duct_straight_round(self):
        assert abs(calc_area("duct_straight", "Воздуховод Ø100") - math.pi * 0.1) < 1e-4

    def test_duct_straight_rect(self):
        assert abs(calc_area("duct_straight", "Воздуховод 300x300") - 1.2) < 1e-4

    def test_elbow_round(self):
        # R=1.0×D: S = π×D × (π×D×90/180)
        assert abs(calc_area("elbow_round", "Отвод 90° Ø200") - 0.1974) < 1e-3

    def test_elbow_rect_uses_max_dim(self):
        # R = 1.0×max(A,B) — подтверждено result-файлом (0.1815 для 20° 400x250)
        assert abs(calc_area("elbow_rect", "Отвод 20° 400x250") - 0.1815) < 1e-3

    def test_cap_round(self):
        assert abs(calc_area("cap_round", "Заглушка Ø100") - math.pi * 0.01 / 4) < 1e-6


class TestGate:
    def test_true_for_ductwork(self):
        assert is_ductwork_row("Воздуховод из оцинкованной стали 300x300, δ=0,8мм")
        assert is_ductwork_row("Отвод круглого воздуховода 90° Ø200", "plumbing_heating_pipes")
        assert is_ductwork_row("Переход круглого сечения 0200/0160")
        assert is_ductwork_row("Зонт пристенный 400x300")
        assert is_ductwork_row("Дефлектор Ø200")

    def test_false_for_plumbing(self):
        assert not is_ductwork_row("Кран шаровой Ду15", "plumbing_heating_valves_armature")
        assert not is_ductwork_row("Отвод-90 ф20х2,8", "plumbing_heating_pipes")
        assert not is_ductwork_row("Ниппель 1/2", "unknown")
        assert not is_ductwork_row("Воздушный клапан круглого сечения: Ø200", "ventilation_climate_ventilation")
        assert not is_ductwork_row("Вентилятор канальный VC-160", "ventilation_climate_ventilation")

    def test_conservative_bare_items(self):
        # Без вент-маркера и без ventilation-типа — не перехватываем (мягкость)
        assert not is_ductwork_row("Заглушка Ø100", "unknown")

    def test_count(self):
        specs = ["Воздуховод Ø100", "Кран шаровой Ду15", "Отвод круглого воздуховода 90° Ø200"]
        assert count_ductwork_items(specs) == 2


class TestCalculateDuctwork:
    def _meta(self, qty, unit="шт"):
        return {"qty": qty, "uom": unit}

    def test_round_duct_price_per_item(self):
        r = calculate_ductwork_row(
            "Воздуховод из оцинкованной стали Ø100, толщина стали 0,5мм",
            self._meta(920, "м.п."),
        )
        assert r is not None
        assert abs(r["price"] - 965.86) < 0.1  # 0.3299 × 1220 × 0.80 × 3.0
        assert r["requires_review"] is True
        assert "рассчитано программно" in r["reason"]
        assert "L_ном" in r["ductwork_breakdown"]

    def test_rect_duct_price_per_item(self):
        r = calculate_ductwork_row(
            "Воздуховод из оцинкованной стали 150х150, толщина стали 0,5мм",
            self._meta(4, "м.п."),
        )
        assert r is not None
        assert abs(r["price"] - 917.91) < 0.1  # 0.63 × 1457 × 0.80 × 1.25

    def test_elbow_fitting_no_nomenclature(self):
        r = calculate_ductwork_row(
            "Отвод круглого воздуховода 90° ⌀200-⌀200",
            self._meta(4),
        )
        assert r is not None
        assert abs(r["price"] - 504.68) < 0.1  # 0.2073 × 2435 × 1.0

    def test_non_ductwork_returns_none(self):
        assert calculate_ductwork_row("Кран шаровой Ду15", self._meta(44)) is None
        assert calculate_ductwork_row("Вентилятор канальный VC-160", self._meta(1)) is None

    def test_config_override_price_per_m2(self):
        r = calculate_ductwork_row(
            "Воздуховод Ø100, толщина стали 0,5мм",
            self._meta(10, "м.п."),
            config={"price_per_m2": 500},
        )
        assert r is not None
        # 0.3299 × 500 × 0.80 × 3.0
        assert abs(r["price"] - 395.84) < 0.1

    def test_config_disable_requires_review(self):
        r = calculate_ductwork_row(
            "Воздуховод Ø100, толщина стали 0,5мм",
            self._meta(10, "м.п."),
            config={"requires_review": False},
        )
        assert r["requires_review"] is False

    def test_nomenclature_lengths_defaults(self):
        assert NOMENCLATURE_LENGTHS["round"] == 3.0
        assert NOMENCLATURE_LENGTHS["rect"] == 1.25


class TestNormalizeDiameterSymbols:
    def test_explicit_symbols(self):
        assert normalize_diameter_symbols("⌀225") == "Ø225"
        assert normalize_diameter_symbols("∅200") == "Ø200"
        assert normalize_diameter_symbols("ø160") == "Ø160"

    def test_ocr_p_to_diameter(self):
        assert normalize_diameter_symbols("p225") == "Ø225"
        assert normalize_diameter_symbols("p200") == "Ø200"
        assert normalize_diameter_symbols("р225") == "Ø225"

    def test_p_in_full_spec(self):
        result = normalize_diameter_symbols("Воздуховод из тонколистовой оцинкованной стали p225")
        assert "Ø225" in result
        assert "p225" not in result

    def test_p_with_separator(self):
        assert "Ø200" in normalize_diameter_symbols("Воздуховод p200, толщина 0,5мм")
        assert "Ø200" in normalize_diameter_symbols("Воздуховод p200; длина 3м")

    def test_no_false_positive(self):
        result = normalize_diameter_symbols("Петля термоусаживаемая 100мм")
        assert "Петля" in result
        assert "Ø" not in result

    def test_fix_circle_notation_p_in_context(self):
        result = fix_circle_notation("Воздуховод p225")
        assert "Ø225" in result
        result2 = fix_circle_notation("Отвод круглого воздуховода p200")
        assert "Ø200" in result2

    def test_ductwork_with_p_dimension(self):
        r = calculate_ductwork_row(
            "Воздуховод из тонколистовой оцинкованной стали p225",
            {"qty": 5, "unit": "м.п."},
            product_type="ventilation_climate_ventilation",
        )
        assert r is not None
        assert r["price"] > 0


class TestDuctContextDetection:
    def test_angle_radius_pattern(self):
        assert is_ductwork_row("Отвод прям. 15° 600x300-600x300/R150/5")
        assert is_ductwork_row("Отвод прямоугольного воздуховода 90° 400x250/R200")

    def test_krugl_abbreviation(self):
        assert is_ductwork_row("Заглушка круглая Ø100")
        assert is_ductwork_row("Врезка круглая Ø200")
        assert is_ductwork_row("Утка круглая Ø200")

    def test_spec_context_ventilation(self):
        assert is_ductwork_row("Заглушка 400x600", spec_context="ventilation")
        assert is_ductwork_row("Тройник 300x200-150x100", spec_context="ventilation")
        assert is_ductwork_row("Переход 300x200-Ø200", spec_context="ventilation")

    def test_no_false_positive_plumbing(self):
        plumbing_items = [
            'Кран шаровой Ду15', 'Клапан обратный Ду50', 'Заслонка Ду150',
            'Тройник ППР Ду25', 'Отвод канализационный 110°',
            'Тройник канализационный 110x110-110', 'Заглушка канализационная 110',
            'Переход R110 (канализация)', 'Фитинг 90° R25',
            'Отвод 45° R50 (пластик)', 'Заглушка Ø110 канализационная',
        ]
        for item in plumbing_items:
            assert not is_ductwork_row(item), f"FP: {item}"

    def test_spec_context_none_no_change(self):
        assert not is_ductwork_row("Заглушка 400x600", spec_context=None)
        assert not is_ductwork_row("Тройник 300x200-150x100", spec_context=None)

    def test_normalize_p_after_delimiters(self):
        assert normalize_diameter_symbols("Переход 300x200-p125") == "Переход 300x200-Ø125"
        assert normalize_diameter_symbols("Тройник 400x250-p150") == "Тройник 400x250-Ø150"
        assert normalize_diameter_symbols("Врезка-p250") == "Врезка-Ø250"
        assert normalize_diameter_symbols("Заглушка(p100)") == "Заглушка(Ø100)"
        assert normalize_diameter_symbols("р200") == "Ø200"

    def test_normalize_no_false_positive_words(self):
        assert "P125" in normalize_diameter_symbols("P125 насос")
        assert "Addap125" in normalize_diameter_symbols("Addap125 клапан")
        assert "пункт125" in normalize_diameter_symbols("пункт125 труба")

    def test_normalize_hyphenated_round_transition(self):
        # Регрессия: "p160-p100" конвертировался частично (p160 не ловился —
        # после числа шёл дефис, отсутствующий в lookahead regex).
        assert normalize_diameter_symbols("Переход кругл. p160-p100") == "Переход кругл. Ø160-Ø100"
        assert normalize_diameter_symbols("p125-p125") == "Ø125-Ø125"
        assert normalize_diameter_symbols("Отвод круглого воздуховода 45° p160-p160") == "Отвод круглого воздуховода 45° Ø160-Ø160"

    def test_round_round_transition_calc_standalone(self):
        text = "Переход кругл. p160-p100/тип 1/l150/"
        r = calculate_ductwork_row(text, {"qty": 1, "unit": "шт"})
        assert r is not None
        assert r["price"] > 0
        assert is_ductwork_row(text)

    def test_round_elbow_hyphenated_calc(self):
        r = calculate_ductwork_row(
            "Отвод круглого воздуховода 45° p160-p160",
            {"qty": 1, "unit": "шт"},
        )
        assert r is not None
        assert r["price"] > 0

    def test_p125_transition_detected_standalone(self):
        text = "Переход 300x200-p125/тип 1/l200/"
        assert is_ductwork_row(text)
        r = calculate_ductwork_row(text, {"qty": 1, "unit": "шт"})
        assert r is not None
        assert r["price"] > 0

    def test_transition_mix_rule(self):
        assert is_ductwork_row("Переход 300x200-Ø125")
        assert is_ductwork_row("Переход Ø200-300x200")

    def test_plumbing_override_in_vent_context(self):
        plumbing_items = [
            'Тройник канализационный 110x110-110',
            'Заглушка канализационная 110',
            'Отвод канализационный 45° Ду110',
            'Переходник Ø110-Ø50',
            'Отвод 45° R50 (пластик)',
            'Переход R110 (канализация)',
        ]
        for item in plumbing_items:
            assert not is_ductwork_row(item, spec_context="ventilation"), f"FP: {item}"

    def test_vent_context_still_detects_ambiguous(self):
        assert is_ductwork_row("Тройник 300x200-150x100", spec_context="ventilation")
        assert is_ductwork_row("Заглушка 400x600", spec_context="ventilation")
        assert is_ductwork_row("Ниппель Ø200", spec_context="ventilation")


class TestInferSpecContext:
    def _texts(self, items):
        return type("Specs", (), {"__iter__": lambda self: iter(items)})()

    def test_vent_spec_returns_ventilation(self):
        specs = [
            "Воздуховод из оцинкованной стали 300x300",
            "Отвод круглого воздуховода 90° Ø200",
            "Воздуховод круглый Ø200",
            "Переход круглого сечения Ø200/Ø160",
            "Переход 300x200-p125/тип 1/l200/",
            "Тройник 300x200-150x100",
        ]
        assert infer_spec_context(self._texts(specs)) == "ventilation"

    def test_plumbing_spec_returns_none(self):
        specs = [
            "Кран шаровой Ду15", "Клапан обратный Ду50", "Тройник ППР Ду25",
            "Отвод канализационный 110°", "Заглушка канализационная 110",
            "Радиатор отопления 10-М-100",
        ]
        assert infer_spec_context(self._texts(specs)) is None

    def test_too_few_rows_returns_none(self):
        specs = ["Воздуховод из оцинкованной стали 300x300"]
        assert infer_spec_context(self._texts(specs)) is None

    def test_empty_returns_none(self):
        assert infer_spec_context([]) is None
