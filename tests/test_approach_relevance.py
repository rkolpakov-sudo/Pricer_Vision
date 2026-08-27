"""Тесты релевантности подходов текущему товару (src/approach_relevance.py)."""

import pytest

from src.approach_relevance import (
    tokenize, approach_relevant, product_name_matches, product_name_matches_ignore_brand,
    _size_key,
    missing_required_tokens, normalize_search_text,
    search_key_tokens,
    model_designators, mismatch_kind,
)


class TestTokenize:
    def test_keeps_meaningful_words(self):
        assert "воздуховод" in tokenize("Воздуховод из оцинкованной стали Ø100")
        assert "оцинкованной" in tokenize("Воздуховод из оцинкованной стали Ø100")

    def test_filters_stopwords_and_short(self):
        tokens = tokenize("Воздуховод из стали и 100")
        assert "из" not in tokens
        assert "и" not in tokens
        assert "100" in tokens

    def test_empty(self):
        assert tokenize("") == set()
        assert tokenize(None) == set()

    def test_case_insensitive(self):
        assert tokenize("Воздуховод") == tokenize("воздуховод")


class TestApproachRelevant:
    def test_relevant_by_shared_word(self):
        approach = {"search_query": "Воздухоотводчик автоматический 1/2 ITAP"}
        assert approach_relevant(approach, "Воздухоотводчик автоматический Ду15") is True

    def test_irrelevant_no_shared_word(self):
        """Подход про регулятор скорости не релевантен воздуховоду."""
        approach = {"search_query": "SRE-Е-2,5/STY-2,5 (220В, 2,5А) Регулятор скорости"}
        assert approach_relevant(approach, "Воздуховод из оцинкованной стали Ø100") is False

    def test_no_query_shows_by_default(self):
        assert approach_relevant({}, "Воздуховод") is True

    def test_extra_text_article(self):
        approach = {"search_query": "SPL.T.16.200.EC"}
        assert approach_relevant(approach, "Воздуховод", extra_text="SPL.T.16.200.EC") is True

    def test_exact_match_relevant(self):
        approach = {"search_query": "Воздуховод из оцинкованной стали Ø100"}
        assert approach_relevant(approach, "Воздуховод из оцинкованной стали Ø100") is True


class TestProductNameMatches:
    def test_ball_valve_vs_balancing_valve(self):
        """Кран шаровой vs Клапан балансировочный — РАЗНЫЕ товары (общий только Ду15)."""
        assert product_name_matches("Кран шаровой Ду15", "Клапан балансировочный Ду15") is False

    def test_same_product_with_extras(self):
        assert product_name_matches("Кран шаровой Ду15", "Кран шаровой дренажный Ду15 Ридан") is True

    def test_balancing_valve_variants(self):
        assert product_name_matches("Клапан балансировочный авт. Ду15",
                                    "Клапан балансировочный автомат латунь APT-R3 Ду15") is True

    def test_air_vent_vs_air_duct(self):
        """Воздухоотводчик vs Воздуховод — РАЗНЫЕ товары."""
        assert product_name_matches("Воздухоотводчик автоматический Ду15",
                                    "Воздуховод из оцинкованной стали Ø100") is False

    def test_air_duct_short_name(self):
        assert product_name_matches("Воздуховод Ø100", "Воздуховод оцинкованный Ø100") is True

    def test_flanged_vs_ball_valve(self):
        """Кран фланцевый не подходит для крана шарового (разный подтип)."""
        assert product_name_matches("Кран шаровой Ду15", "Кран фланцевый Ду100") is False

    def test_no_found_name_allowed(self):
        assert product_name_matches("Кран шаровой Ду15", "") is True

    def test_no_spec_allowed(self):
        assert product_name_matches("", "Кран шаровой Ду15") is True

    def test_stem_variants(self):
        assert product_name_matches("Клапан балансировочный автоматический Ду15",
                                    "Клапан балансировочный автомат Ду15") is True

    def test_different_size_rejected(self):
        """Фатальный случай из лога: страница Ду20 не подходит для строки Ду15."""
        assert product_name_matches("Кран шаровой Ду15",
                                    "Кран шаровой BVR-R Ду 20 (DN 20) Ридан") is False

    def test_same_size_accepted(self):
        assert product_name_matches("Кран шаровой Ду15",
                                    "Кран шаровой BVR-R Ду 15 (DN 15) Ридан") is True

    def test_different_inch_size_rejected(self):
        assert product_name_matches("Труба стальная водогазопроводная 1/2\"",
                                    "Труба стальная водогазопроводная 3/4\"") is False

    def test_different_dimension_rejected(self):
        assert product_name_matches("Радиатор стальной панельный 500x800",
                                    "Радиатор стальной панельный 500x1000") is False

    def test_different_duct_diameter_rejected(self):
        assert product_name_matches("Воздуховод Ø100", "Воздуховод оцинкованный Ø200") is False

    def test_brand_mismatch_rejected(self):
        assert product_name_matches("Кран шаровой Ду15, завод-изготовитель Ридан",
                                    "Кран шаровой Ду15, завод-изготовитель Пульсар") is False

    def test_brand_match_accepted(self):
        assert product_name_matches("Кран шаровой Ду15, завод-изготовитель Ридан",
                                    "Кран шаровой Ду15, завод-изготовитель Ридан") is True

    def test_structural_words_are_not_similarity_evidence(self):
        """Общие «завод-изготовитель» не делают теплосчетчик похожим на кран."""
        assert product_name_matches("Теплосчетчик, завод-изготовитель Пульсар",
                                    "Кран шаровой Ду15, завод-изготовитель Ридан") is False

    def test_static_vs_automatic_balancing_valve(self):
        """Фатальный случай из лога: спецификация «статический», в кэше «авт.»
        (автоматический APT-R). Различающее слово «статический» обязано отклонить."""
        assert product_name_matches("клапан баланс. статический Ду15",
                                    "Клапан балансировочный авт. Ду15") is False

    def test_static_balancing_valve_matches_static(self):
        assert product_name_matches("клапан баланс. статический Ду15",
                                    "Клапан балансировочный статический Ду15") is True

    def test_abbrev_aut_matches_automatic_both_ways(self):
        """«авт.» и «автоматический» — один и тот же товар (префикс 3 символа)."""
        assert product_name_matches("Клапан балансировочный авт. Ду15",
                                    "Клапан балансировочный автоматический Ду15") is True
        assert product_name_matches("Клапан балансировочный автоматический Ду15",
                                    "Клапан балансировочный авт. Ду15") is True

    def test_parametric_token_optional(self):
        """Ру/Kvs в спецификации не обязаны быть в названии карточки."""
        assert product_name_matches("Клапан балансировочный авт. Ду15, Ру16, Kvs=1,9",
                                    "Клапан балансировочный автомат Ду15 Ридан") is True

    def test_ball_valve_short_spec_full_coverage(self):
        """Все значимые слова спецификации должны присутствовать в найденном."""
        assert product_name_matches("Кран шаровой Ду15",
                                    "Кран Ду15") is False

    def test_diameter_symbol_variants_equivalent(self):
        """⌀ (U+2300) и Ø (U+00D8) — один и тот же диаметр. Кэшированная цена,
        сохранённая с «Ø150х4,5», должна переиспользоваться для строки «⌀150х4,5»."""
        assert product_name_matches(
            "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5",
            "Труба стальная водогазопроводная оцинкованная на грувлоках Ø150х4,5 (ГОСТ 3262-75)",
        ) is True

    def test_diameter_symbol_both_directions(self):
        assert product_name_matches(
            "Труба стальная водогазопроводная оцинкованная Ø150х4,5",
            "Труба стальная водогазопроводная оцинкованная ⌀150х4,5",
        ) is True

    def test_diameter_symbol_different_size_still_rejected(self):
        """Символ не ослабляет контроль размера: Ø200 не подходит для ⌀150."""
        assert product_name_matches(
            "Труба стальная водогазопроводная оцинкованная ⌀150х4,5",
            "Труба стальная водогазопроводная оцинкованная Ø200х6,0",
        ) is False


class TestProductNameMatchesIgnoreBrand:
    def test_brand_difference_accepted(self):
        """«Ридан» vs «Пульсар» — при игнорировании бренда совпадение есть."""
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду15, завод-изготовитель Ридан",
            "Кран шаровой Ду15, завод-изготовитель Пульсар",
        ) is True

    def test_brand_present_only_in_spec(self):
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду15, завод-изготовитель Ридан",
            "Кран шаровой Ду15",
        ) is True

    def test_brand_present_only_in_found(self):
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду15",
            "Кран шаровой Ду15 Ридан",
        ) is True

    def test_fl_abbrev_accepted(self):
        """«фл» в названии карточки расширяется до «фланцевый»."""
        assert product_name_matches_ignore_brand(
            "Клапан балансировочный авт. фланцевый Ду100",
            "Клапан балансировочный автомат чугун R206C Ду 100 Ру16 фл Kvs=104.6м3/ч Giacomini",
        ) is True

    def test_different_product_type_rejected(self):
        """Разный тип товара игнорированием бренда не спасается."""
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду15",
            "Клапан балансировочный Ду15",
        ) is False

    def test_static_vs_automatic_rejected(self):
        """Разные подтипы (статический ≠ автоматический) — не совпадение."""
        assert product_name_matches_ignore_brand(
            "клапан баланс. статический Ду15",
            "Клапан балансировочный авт. Ду15",
        ) is False

    def test_different_size_rejected(self):
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду15",
            "Кран шаровой Ду 20 Ридан",
        ) is False


class TestMissingRequiredTokens:
    def test_full_title_no_missing(self):
        assert missing_required_tokens(
            "Компенсатор сильфонный под приварку Ду40",
            "Компенсатор сильфонный осевой многослойный с кожухом сталь нерж Ду 40 Ру16 под приварку Hortum",
        ) == []

    def test_truncated_name_reports_missing_word(self):
        """Кейс из лога: LLM передал сокращённое название без «приварку» → сообщить об этом."""
        assert missing_required_tokens(
            "Компенсатор сильфонный под приварку Ду40",
            "Компенсатор сильфонный осевой многослойный б/кожух",
        ) == ["приварку"]

    def test_exact_same(self):
        assert missing_required_tokens("Кран шаровой Ду15", "Кран шаровой Ду15") == []

    def test_different_product(self):
        missing = missing_required_tokens("Кран шаровой Ду15", "Клапан балансировочный Ду15")
        assert "кран" in missing
        assert "шаровой" in missing

    def test_empty_input(self):
        assert missing_required_tokens("", "Кран") == []
        assert missing_required_tokens("Кран", "") == []


class TestGroovlockContextRule:
    """«на грувлоках» незначимо для ВГП-оцинкованной трубы, но значимо вне её."""

    VGP_SPEC = "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5"
    VGP_PLAIN = "Труба стальная водогазопроводная оцинкованная ⌀150х4,5"

    def test_vgp_pipe_with_groovlock_matches_plain(self):
        assert product_name_matches(self.VGP_SPEC, self.VGP_PLAIN) is True

    def test_vgp_pipe_plain_matches_with_groovlock(self):
        assert product_name_matches(self.VGP_PLAIN, self.VGP_SPEC) is True

    def test_missing_required_tokens_empty_in_context(self):
        assert missing_required_tokens(self.VGP_SPEC, self.VGP_PLAIN) == []

    def test_groovlock_still_significant_elsewhere(self):
        assert product_name_matches("Труба на грувлоках ⌀150", "Труба ⌀150") is False
        assert missing_required_tokens("Труба на грувлоках ⌀150", "Труба ⌀150") == ["грувлоках"]

    def test_different_size_still_rejected(self):
        other = "Труба стальная водогазопроводная оцинкованная ⌀100х3"
        assert product_name_matches(self.VGP_SPEC, other) is False


class TestNormalizeSearchText:
    """Поисковый текст без контекстно-незначимых фраз (агент не ищет «грувлок»)."""

    def test_strips_groovlock_phrase_when_base_present(self):
        assert normalize_search_text(
            "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5"
        ) == "Труба стальная водогазопроводная оцинкованная ⌀150х4,5"

    def test_no_double_spaces_after_removal(self):
        assert "  " not in normalize_search_text(
            "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5"
        )

    def test_keeps_text_without_rule(self):
        assert normalize_search_text("Кран шаровой 1/2 ру 16") == "Кран шаровой 1/2 ру 16"

    def test_keeps_groovlock_outside_context(self):
        assert normalize_search_text("Труба на грувлоках ⌀150") == "Труба на грувлоках ⌀150"

    def test_empty_and_none(self):
        assert normalize_search_text("") == ""
        assert normalize_search_text(None) is None


class TestSizeKeySlashDimensions:
    """Слэш-типоразмеры («60/40-2», «28/32», «20/20/16») — размеры, а не мусор.

    Регрессия: цена изоляции Ø25 переиспользовалась на трубки ENERGOFLEX
    всех диаметров, т.к. формат «60/40-2» не извлекался как размер.
    """

    def test_slash_pair_extracted(self):
        assert _size_key("Трубка ENERGOFLEX Super SK 60/40-2") == {"60/40"}

    def test_slash_single_extracted(self):
        assert _size_key("Теплоизоляция Energomax 06/6-2") == {"06/6"}

    def test_triple_slash_extracted(self):
        assert _size_key("Тройник полипропиленовый 20/20/16") == {"20/20/16"}

    def test_inch_fraction_not_slash_dim(self):
        sizes = _size_key('Труба стальная водогазопроводная 1/2"')
        assert "1/2" not in {s for s in sizes if "/" in s and '"' not in s}

    def test_isolation_vs_tube_rejected_default(self):
        spec = "Трубка ENERGOFLEX Super SK 60/40-2"
        cached = "Изоляция 13 мм для труб Ø25, ENERGOFLEX SUPER"
        assert product_name_matches(spec, cached) is False


class TestStrictSizes:
    """strict_sizes=True: расхождение размеров с известной стороны — отказ.

    Для авто-реюза (rule 8, semantic cache): если в спецификации размер ИЗВЕСТЕН
    (Ду15) — найденный товар без размера или с другим размером отклоняется.
    Если в спецификации размер не извлекается (МС-140: «Мх500» без пары) —
    нечего проверять, товар может совпадать («МС-140х500» в карточке).
    """

    def test_one_sided_size_strict_rejects(self):
        stored = "Кран шаровой полнопроходной латунный никелированный с накидной гайкой"
        query = stored + " DN15"
        assert product_name_matches(query, stored) is True
        assert product_name_matches(query, stored, strict_sizes=True) is False

    def test_spec_unknown_size_with_found_size_passes(self):
        """МС-140: spec «Мх500» не даёт пару размеров (None), в карточке «140х500» —
        товар тот же, strict НЕ отклоняет (нечего проверять)."""
        spec = "Чугунный секционный радиатор с боковым подключением, тип МС-140 Мx500 МС-140 Мх500-0,9-2"
        stored = "Чугунный секционный радиатор с боковым подключением, тип МС-140 Мx500 (Радиатор секционный чугунный МС-140х500) МС-140 Мх500-0,9-2"
        assert _size_key(spec) is None
        assert _size_key(stored) == {"140x500"}
        assert product_name_matches(spec, stored, strict_sizes=True) is True

    def test_cyrillic_x_normalized(self):
        """«Мх500» (кирилл.) и «Мx500» (лат.) — одна модель (нормализация х/x)."""
        spec = "Чугунный секционный радиатор, тип МС-140 Мх500 МС-140 Мх500-0,9-2"
        stored = "Чугунный секционный радиатор, тип МС-140 Мx500 МС-140 Мх500-0,9-2"
        assert model_designators(spec) == model_designators(stored) == {"мс140", "мx500", "-0,9-2"}

    def test_homoglyph_size_equal(self):
        """«140х500» (кир.) и «140x500» (лат.) — одинаковый размер."""
        assert _size_key("МС-140х500") == _size_key("МС-140x500") == {"140x500"}

    def test_words_not_affected(self):
        """Нормализация не трогает «х» в начале слова."""
        from src.approach_relevance import _norm_dim_sep
        assert _norm_dim_sep("характеристика 500х600") == "характеристика 500x600"
        assert _norm_dim_sep("хомутик") == "хомутик"

    def test_both_empty_sizes_pass_strict(self):
        assert product_name_matches(
            "Клей Energopro", "Клей Energopro", strict_sizes=True
        ) is True

    def test_equal_sizes_pass_strict(self):
        assert product_name_matches(
            "Кран шаровой Ду15 Ридан",
            "Кран шаровой Ду15, завод-изготовитель Ридан",
            strict_sizes=True,
        ) is True

    def test_different_sizes_rejected_in_any_mode(self):
        assert product_name_matches(
            "Трубка ENERGOFLEX Super SK 28/32-2",
            "Изоляция 13 мм для труб Ø25, ENERGOFLEX SUPER",
            strict_sizes=True,
        ) is False

    def test_ignore_brand_accepts_strict_flag(self):
        stored = "Кран шаровой полнопроходной никелированный"
        query = stored + " DN25"
        assert product_name_matches_ignore_brand(query, stored, strict_sizes=True) is False


class TestIgnoreSizes:
    """ignore_sizes=True — размер не участвует в сравнении (гид/переупорядочивание)."""

    def test_different_sizes_ignored(self):
        query = "Стальной панельный радиатор LEMAX Premium C10 500x600"
        stored = "Стальной панельный радиатор LEMAX Premium C10 500x400"
        assert product_name_matches(query, stored) is False
        assert product_name_matches(query, stored, ignore_sizes=True) is True

    def test_ignored_with_one_sided_size(self):
        assert product_name_matches(
            "Кран шаровой Ду15",
            "Кран шаровой",
            ignore_sizes=True,
        ) is True

    def test_strict_and_ignore_conflict_resolves_to_ignore(self):
        query = "Кран шаровой Ду20"
        stored = "Кран шаровой Ду15"
        assert product_name_matches(query, stored, strict_sizes=True, ignore_sizes=True) is True

    def test_ignore_brand_also_ignores_sizes(self):
        assert product_name_matches_ignore_brand(
            "Кран шаровой Ду20, завод-изготовитель Ридан",
            "Кран шаровой Ду15, завод-изготовитель Пульсар",
            ignore_sizes=True,
        ) is True


class TestCardH1LenientMatch:
    """Регрессия: агент в карточке mircli не сохранил цену из-за ложного mismatch.

    h1 сайта сокращён: серия/комплектация/подключение опущены, бренд в
    транслитерации, размер в формате «10х500х600» (тип × высота × ширина).
    Система НЕ решает молча — матчер остаётся строгим, а отсутствующие
    ОПИСАТЕЛЬНЫЕ слова выносятся в advisory-совет, где LLM перепроверяет карточку.
    """

    SPEC = ("Стальной панельный радиатор с боковым подключением LEMAX Premium Compact Hygiene, "
            "тип C10, в компл. с краном для выпуска воздуха и креплениями LEMAX Premium C10 500x600")

    def test_mircli_h1_missing_only_descriptive_words(self):
        """Матчер строгий (не решает за LLM), но отсутствуют ТОЛЬКО описательные слова —
        размер 500x600 и ключевые атрибуты совпадают (транслит, тройной формат)."""
        h1 = "Стальной панельный радиатор Лемакс Premium C 10х500х600"
        missing = missing_required_tokens(self.SPEC, h1)
        # Ключевые слова (тип/материал/бренд/размер) покрыты: стальной/панельный/радиатор есть,
        # lemax = Лемакс (транслит), 500x600 = из 10х500х600.
        assert "lemax" not in missing
        assert "стальной" not in missing
        assert "радиатор" not in missing
        # Отсутствуют только описательные слова (серия/комплектация/подключение) — их решение
        # передаётся LLM через advisory, а не молча игнорируется матчером.
        assert missing and all(
            w in {"compact", "hygiene", "компл", "краном", "креплениями",
                  "выпуска", "воздуха", "боковым", "подключением"}
            for w in missing
        )

    def test_wrong_width_rejected(self):
        h1 = "Стальной панельный радиатор Лемакс Premium C 10х500х800"
        assert product_name_matches(self.SPEC, h1) is False

    def test_triple_dimension_last_pair(self):
        """«10х500х600» → размер {500x600} (тип × высота × ширина)."""
        assert _size_key("Лемакс Premium C 10х500х600") == {"500x600"}
        assert _size_key("Лемакс Premium C 10х500х600 500x600") == {"500x600"}

    def test_brand_transliteration_cyrillic_latin(self):
        """«Лемакс»(кир) ≈ «lemax»(лат) — один бренд."""
        assert product_name_matches(
            "Радиатор стальной LEMAX C10 500x600",
            "Радиатор стальной Лемакс C 10х500х600",
        ) is True

class TestSearchKeyTokens:
    SPEC = 'Стальной панельный радиатор с боковым подключением LEMAX Premium Compact Hygiene, тип C10, в компл. с краном для выпуска воздуха и креплениями LEMAX Premium C10 500x600'
    META = {'brand': 'Лемакс', 'spec': 'LEMAX Premium C10 500x600', 'article': '065B8203R', 'headers': []}

    def test_extracts_size_from_spec(self):
        out = search_key_tokens(self.SPEC, self.META)
        assert '500x600' in out.get('size', '')

    def test_brand_with_translit(self):
        out = search_key_tokens(self.SPEC, self.META)
        assert 'LEMAX' in out['brand'] or 'Лемакс' in out['brand']

    def test_article_and_type(self):
        out = search_key_tokens(self.SPEC, self.META)
        assert out['article'] == '065B8203R'
        assert 'C10' in out['type']

    def test_standard_reference_excluded_from_type(self):
        out = search_key_tokens('Труба стальная', {'spec': 'ГОСТ 3262-75', 'brand': ''})
        assert 'type' not in out

    def test_no_meta_falls_back_to_keywords(self):
        out = search_key_tokens('Кран шаровой Ду15', None)
        assert out  # не пусто: keywords или размер

    def test_empty_returns_empty(self):
        assert search_key_tokens('', {}) == {}

class TestPhase0ModelProtection:
    SPEC_C10 = 'Стальной панельный радиатор с боковым подключением LEMAX Premium Compact Hygiene, тип C10, в компл. с краном для выпуска воздуха и креплениями LEMAX Premium C10 500x600'
    H1_C10 = 'Радиатор панельный ЛЕМАКС Premium C 10х500х600'

    def test_model_designators_canon(self):
        assert model_designators('LEMAX Premium C10 500x600') == {'c10'}
        assert model_designators('Premium C 10х500х600') == {'c10'}
        assert model_designators('VC33 600x3000') == {'vc33'}
        assert model_designators('MS-140') == {'ms140'}

    def test_model_designators_excludes_params(self):
        assert model_designators('Кран шаровой Ду15 PN16 ру16 kvs') == set()
        assert model_designators('11б38п Ду15') == set()

    def test_reuse_blocks_different_model(self):
        spec_c20 = self.SPEC_C10.replace('C10', 'C20')
        assert product_name_matches(spec_c20, self.SPEC_C10, strict_sizes=True) is False
        # ignore_sizes — семейный гид: разные модели того же типа (C10/C20)
        # показываются как «куда идти», но не для реюза (строгий путь блокирует).
        assert product_name_matches(spec_c20, self.SPEC_C10, ignore_sizes=True) is True

    def test_reuse_allows_same_model(self):
        assert product_name_matches(self.SPEC_C10, self.SPEC_C10, strict_sizes=True) is True

    def test_reuse_same_model_via_h1(self):
        # h1 «C 10х500х600» — модель c10 (сырой текст), токенизатор его не видит
        assert product_name_matches(self.SPEC_C10, self.H1_C10, strict_sizes=True) is False
        # но полный spec → spec (путь реюза сравнивает spec со stored spec) — True

    def test_mismatch_kind_descriptive_only(self):
        assert mismatch_kind(self.SPEC_C10, self.H1_C10) == 'descriptive_only'

    def test_mismatch_kind_key_model(self):
        spec_c20 = self.SPEC_C10.replace('C10', 'C20')
        assert mismatch_kind(spec_c20, self.H1_C10) == 'key'

    def test_mismatch_kind_key_size(self):
        assert mismatch_kind('Кран шаровой Ду15', 'Кран шаровой Ду20') == 'key'

    def test_mismatch_kind_none(self):
        assert mismatch_kind(self.SPEC_C10, self.SPEC_C10) == 'none'
