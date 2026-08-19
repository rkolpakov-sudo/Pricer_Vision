"""Тесты релевантности подходов текущему товару (src/approach_relevance.py)."""

import pytest

from src.approach_relevance import (
    tokenize, approach_relevant, product_name_matches, product_name_matches_ignore_brand,
    missing_required_tokens,
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
