"""Тесты оптимизации контекста агента (_estimate_tokens/_message_size/_trim_messages_for_budget).

Выделены в отдельный файл по структуре Фазы 7 (критичный модуль context optimizer).
"""

from src.agent_loop import (
    _estimate_tokens, _message_size, _trim_messages_for_budget,
    CONTEXT_TOKEN_BUDGET,
)


class TestEstimateTokens:
    def test_none(self):
        assert _estimate_tokens(None) == 0

    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_short(self):
        assert _estimate_tokens("short") == 1

    def test_cyrillic(self):
        assert _estimate_tokens("Привет мир") == 2

    def test_long(self):
        assert _estimate_tokens("a" * 100) == 25


class TestMessageSize:
    def test_content_and_role(self):
        assert _message_size({"role": "user", "content": "a" * 40}) == 11

    def test_tool_calls_add_size(self):
        msg = {
            "role": "assistant",
            "content": "x",
            "tool_calls": [{"function": {"name": "browser_navigate", "arguments": "{}"}}],
        }
        plain = _message_size({"role": "assistant", "content": "x"})
        assert _message_size(msg) > plain

    def test_empty_dict(self):
        assert _message_size({}) == 0

    def test_none_content(self):
        assert _message_size({"role": "user"}) == 1


class TestTrimMessagesForBudget:
    def test_empty_list(self):
        assert _trim_messages_for_budget([]) == []

    def test_none(self):
        assert _trim_messages_for_budget(None) is None

    def test_under_budget_unchanged(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        assert _trim_messages_for_budget(messages) == messages

    def test_keeps_system_and_last_user(self):
        big = "x" * 40000
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "final"},
        ]
        out = _trim_messages_for_budget(messages)
        assert out[0]["content"] == "sys"
        assert out[-1]["content"] == "final"
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= CONTEXT_TOKEN_BUDGET

    def test_trims_old_messages_over_budget(self):
        big = "y" * 20000
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": big},
            {"role": "tool", "content": big},
            {"role": "user", "content": "last"},
        ]
        out = _trim_messages_for_budget(messages)
        assert out[-1]["content"] == "last"
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= CONTEXT_TOKEN_BUDGET

    def test_custom_budget_trims_intermediate(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "y" * 200},
            {"role": "tool", "content": "z" * 200},
            {"role": "user", "content": "last"},
        ]
        out = _trim_messages_for_budget(messages, budget=10)
        contents = [m.get("content") for m in out]
        assert contents == ["s", "last"]
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= 10

    def test_budget_constant(self):
        assert CONTEXT_TOKEN_BUDGET == 8000
