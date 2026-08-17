"""Тесты релевантности подходов текущему товару (src/approach_relevance.py)."""

import pytest

from src.approach_relevance import tokenize, approach_relevant, product_name_matches


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
