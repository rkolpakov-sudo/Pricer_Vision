"""Тесты SkipRegistry — отметка «пропустить» и транзитивные полные аналоги (Qt-free)."""

import pytest

from src.skip_registry import SkipRegistry


class TestMarkUnmark:
    def test_mark_then_skipped(self):
        r = SkipRegistry()
        r.mark("Кран фланцевый Ду100", "Ридан")
        assert r.is_skipped("Кран фланцевый Ду100", "Ридан")
        assert r.blocked_count() == 1

    def test_mark_no_brand(self):
        r = SkipRegistry()
        r.mark("Кабель ВВГ")
        assert r.is_skipped("Кабель ВВГ")
        assert r.is_skipped("Кабель ВВГ", "")

    def test_unmark(self):
        r = SkipRegistry()
        r.mark("Кабель ВВГ")
        assert r.is_skipped("Кабель ВВГ")
        r.unmark("Кабель ВВГ")
        assert not r.is_skipped("Кабель ВВГ")
        assert r.blocked_count() == 0

    def test_unmark_only_exact(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        r.unmark("Кран шаровой Ду15", "Пульсар")  # другой бренд — не снимает
        assert r.is_skipped("Кран шаровой Ду15", "Ридан")

    def test_duplicate_mark_deduped(self):
        r = SkipRegistry()
        r.mark("Кабель ВВГ", "Спецкабель")
        r.mark("Кабель ВВГ", "Спецкабель")
        assert r.blocked_count() == 1

    def test_empty_text_noop(self):
        r = SkipRegistry()
        r.mark("   ", "Ридан")
        r.mark("", "")
        assert r.blocked_count() == 0

    def test_reset(self):
        r = SkipRegistry()
        r.mark("А")
        r.mark("Б")
        assert len(r) == 2
        r.reset()
        assert len(r) == 0
        assert not r.is_skipped("А")

    def test_normalized_case_whitespace(self):
        r = SkipRegistry()
        r.mark(" Кран   фланцевый ДУ100 ", "РИДАН")
        assert r.is_skipped("кран фланцевый ду100", "ридан")


class TestFullAnalog:
    def test_rephrased_abbreviation(self):
        r = SkipRegistry()
        r.mark("Клапан балансировочный авт. Ду15", "Ридан")
        assert r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")

    def test_extra_param_word_ok(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert r.is_skipped("Кран шаровой Ду15 ру16", "Ридан")

    def test_different_size_not_skipped(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert not r.is_skipped("Кран шаровой Ду20", "Ридан")

    def test_different_type_not_skipped(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert not r.is_skipped("Клапан балансировочный Ду15", "Ридан")

    def test_different_brand_not_skipped(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert not r.is_skipped("Кран шаровой Ду15", "Пульсар")

    def test_marked_brand_vs_no_brand_not_skipped(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert not r.is_skipped("Кран шаровой Ду15")

    def test_single_word_mark_no_cascade(self):
        r = SkipRegistry()
        r.mark("Кран")
        assert not r.is_skipped("Кран шаровой Ду15")
        assert r.is_skipped("кран")

    def test_generic_section_words_not_analogous(self):
        r = SkipRegistry()
        r.mark("Отопление")
        assert not r.is_skipped("Вентиляция")

    def test_matches_returns_analog_description(self):
        r = SkipRegistry()
        r.mark("Клапан балансировочный авт. Ду15", "Ридан")
        matched = r.matches("Клапан балансировочный автоматический Ду15", "Ридан")
        assert matched is not None
        assert "авт" in matched

    def test_matches_none_when_not_skipped(self):
        r = SkipRegistry()
        r.mark("Кран шаровой Ду15", "Ридан")
        assert r.matches("Кран шаровой Ду20", "Ридан") is None

    def test_unmark_analog_does_not_break_group(self):
        """Снятие отметки с АНАЛОГА не затрагивает основную позицию.
        Аналог перестаёт быть skipped, но группа (основная) остаётся."""
        r = SkipRegistry()
        r.mark("Клапан балансировочный авт. Ду15", "Ридан")
        # полный аналог
        assert r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        # снимаем отметку с аналога
        r.unmark("Клапан балансировочный автоматический Ду15", "Ридан")
        assert not r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        # основная осталась
        assert r.is_skipped("Клапан балансировочный авт. Ду15", "Ридан")
        assert r.blocked_count() == 1

    def test_unmark_primary_clears_entire_group(self):
        """Снятие отметки с ОСНОВНОЙ позиции удаляет всю группу, включая аналоги."""
        r = SkipRegistry()
        r.mark("Клапан балансировочный авт. Ду15", "Ридан")
        assert r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        r.unmark("Клапан балансировочный авт. Ду15", "Ридан")
        assert not r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        assert not r.is_skipped("Клапан балансировочный авт. Ду15", "Ридан")
        assert r.blocked_count() == 0

    def test_mark_after_unmark_analog_restores_it(self):
        """Повторная отметка ранее исключённого аналога возвращает его в группу."""
        r = SkipRegistry()
        r.mark("Клапан балансировочный авт. Ду15", "Ридан")
        r.unmark("Клапан балансировочный автоматический Ду15", "Ридан")
        assert not r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        r.mark("Клапан балансировочный автоматический Ду15", "Ридан")
        assert r.is_skipped("Клапан балансировочный автоматический Ду15", "Ридан")
        assert r.blocked_count() == 1


class TestRunnerIntegrationShape:
    def test_runner_stores_registry(self):
        from src.mcp_agent_runner import MCPAgentRunner
        reg = SkipRegistry()
        runner = MCPAgentRunner(specs=[], llm_client=None, skip_registry=reg)
        assert runner._skip_registry is reg

    def test_runner_default_no_registry(self):
        from src.mcp_agent_runner import MCPAgentRunner
        runner = MCPAgentRunner(specs=[], llm_client=None)
        assert runner._skip_registry is None
