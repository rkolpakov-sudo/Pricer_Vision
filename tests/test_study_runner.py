"""Тесты логики завершения обучения (Qt-free).

Проверяем, что после достижения целей (цена + ≥3 подхода + ≥2 хинта) агент
НЕ может продолжать browser-действия и блуждать по каталогу / искать чужие
товары (регрессия: после сифона HL138 агент уходил искать «кран шаровой»).
"""
import pytest

from src.study_runner import StudyRunner, GRAPH_TOOL_NAMES


def _sr():
    sr = StudyRunner.__new__(StudyRunner)
    sr._price_saved = False
    sr._proposed_approaches = []
    sr._proposed_hints = []
    return sr


def test_goals_not_met_by_default():
    sr = _sr()
    assert sr._goals_met() is False


def test_goals_require_price_approaches_hints():
    sr = _sr()
    sr._price_saved = True
    assert sr._goals_met() is False  # нет подходов
    sr._proposed_approaches = [{}, {}, {}]
    assert sr._goals_met() is False  # нет хинтов
    sr._proposed_hints = [{}, {}]
    assert sr._goals_met() is True


def test_browser_tools_blocked_after_goals():
    sr = _sr()
    sr._price_saved = True
    sr._proposed_approaches = [{}, {}, {}]
    sr._proposed_hints = [{}, {}]
    assert sr._goals_met() is True
    for tool in ("browser_navigate", "browser_type", "browser_click",
                 "browser_evaluate", "browser_press_key", "browser_fill_form"):
        assert sr._browser_block_allowed(tool) is True, tool


def test_graph_and_question_tools_not_blocked_after_goals():
    sr = _sr()
    sr._price_saved = True
    sr._proposed_approaches = [{}, {}, {}]
    sr._proposed_hints = [{}, {}]
    assert sr._goals_met() is True
    for tool in GRAPH_TOOL_NAMES:
        assert sr._browser_block_allowed(tool) is False, tool
    assert sr._browser_block_allowed("ask_user") is False


def test_browser_not_blocked_before_goals():
    sr = _sr()
    sr._price_saved = True
    sr._proposed_approaches = [{}]  # только 1 подход — цели не достигнуты
    assert sr._goals_met() is False
    assert sr._browser_block_allowed("browser_navigate") is False


def test_study_prompt_forbids_other_products():
    """Промпт обучения явно запрещает переход на другие виды товаров и
    требует завершения после сохранения цены + подходов + хинтов."""
    from src.study_runner import STUDY_PROMPT
    assert "завершено" in STUDY_PROMPT
    assert "НЕ исследуй другие категории/товары" in STUDY_PROMPT
    assert "не открывай каталоги «краны»" in STUDY_PROMPT
    assert "ЗАВЕРШЕНИЕ" in STUDY_PROMPT
