"""Тесты конфигурируемых правил сопоставления (src/approach_relevance.py).

Правила хранятся в YAML и загружаются через load_rules(); дефолты — встроенные.
Каждый тест сбрасывает правила к дефолтам, чтобы не влиять на остальные.
"""

import pytest

from src import approach_relevance as ar
from src.approach_relevance import (
    load_rules, save_rules, reset_rules, set_rules, get_rules,
    tokenize, product_name_matches, product_name_matches_ignore_brand,
    missing_required_tokens,
)


@pytest.fixture(autouse=True)
def _reset_rules_after():
    yield
    reset_rules()


class TestLoadSaveReset:
    def test_missing_file_uses_defaults(self, tmp_path):
        rules = load_rules(tmp_path / "no_such_file.yaml")
        assert rules["stopwords"] == ar._RULES_DEFAULTS["stopwords"]

    def test_loaded_section_replaces_default(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text("stopwords:\n  - толькоэто\n", encoding="utf-8")
        load_rules(p)
        assert "толькоэто" not in tokenize("толькоэто и другие слова")
        assert "другие" in tokenize("толькоэто и другие слова")

    def test_broken_yaml_keeps_defaults(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text("stopwords: [не закрыли", encoding="utf-8")
        load_rules(p)
        assert ar._STOPWORDS_SET == frozenset(ar._RULES_DEFAULTS["stopwords"])

    def test_save_roundtrip(self, tmp_path):
        p = tmp_path / "rules.yaml"
        save_rules(p)
        loaded = load_rules(p)
        assert loaded["stopwords"] == ar._RULES_DEFAULTS["stopwords"]
        assert loaded["abbreviations"]["фл"] == "фланцевый"
        assert loaded["context_insignificant"][0]["drop"] == "на грувлоках"

    def test_reset_returns_defaults(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text("param_words:\n  - толькоэто\n", encoding="utf-8")
        load_rules(p)
        reset_rules()
        assert ar._PARAM_SET == frozenset(ar._RULES_DEFAULTS["param_words"])


class TestSetGetRules:
    def test_set_rules_applies_live(self):
        set_rules({"stopwords": ["толькоэто"]})
        assert "толькоэто" not in tokenize("толькоэто и другие слова")
        assert "другие" in tokenize("толькоэто и другие слова")

    def test_empty_list_honored(self):
        set_rules({"stopwords": []})
        assert tokenize("для") == {"для"}

    def test_none_value_keeps_default(self):
        set_rules({"stopwords": None})
        assert ar._STOPWORDS_SET == frozenset(ar._RULES_DEFAULTS["stopwords"])

    def test_get_rules_returns_independent_copy(self):
        set_rules({"stopwords": ["а"]})
        r = get_rules()
        r["stopwords"].append("б")
        assert ar._STOPWORDS_SET == frozenset({"а"})


class TestContextRulesFromConfig:
    def test_phrase_based_groovlock_rule(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "context_insignificant:\n"
            "  - base: \"Труба стальная водогазопроводная оцинкованная\"\n"
            "    drop: \"на грувлоках\"\n",
            encoding="utf-8",
        )
        load_rules(p)
        spec = "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5"
        found = "Труба стальная водогазопроводная оцинкованная ⌀150х4,5"
        assert product_name_matches(spec, found) is True
        assert missing_required_tokens(spec, found) == []
        assert product_name_matches("Труба на грувлоках ⌀150", "Труба ⌀150") is False

    def test_drop_case_insensitive(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "context_insignificant:\n"
            "  - base: \"ТРУБА СТАЛЬНАЯ ВОДОГАЗОПРОВОДНАЯ ОЦИНКОВАННАЯ\"\n"
            "    drop: \"НА ГРУВЛОКАХ\"\n",
            encoding="utf-8",
        )
        load_rules(p)
        assert product_name_matches(
            "Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5",
            "Труба стальная водогазопроводная оцинкованная ⌀150х4,5",
        ) is True

    def test_broken_rule_item_skipped(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "context_insignificant:\n"
            "  - base: \"Труба стальная водогазопроводная оцинкованная\"\n"
            "    drop: \"на грувлоках\"\n"
            "  - base: \"только база без drop\"\n",
            encoding="utf-8",
        )
        load_rules(p)
        assert len(ar._CONTEXT_RULES) == 1


class TestAbbreviationFromConfig:
    def test_custom_abbreviation_applied(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text("abbreviations:\n  вгп: водогазопроводная\n", encoding="utf-8")
        load_rules(p)
        assert product_name_matches_ignore_brand(
            "Труба ВГП оцинкованная ⌀150х4,5",
            "Труба водогазопроводная оцинкованная ⌀150х4,5",
        ) is True

    def test_without_abbreviation_rejected(self):
        assert product_name_matches_ignore_brand(
            "Труба ВГП оцинкованная ⌀150х4,5",
            "Труба водогазопроводная оцинкованная ⌀150х4,5",
        ) is False
