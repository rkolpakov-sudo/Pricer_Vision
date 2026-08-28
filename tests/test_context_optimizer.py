"""Тесты оптимизации контекста агента (_estimate_tokens/_message_size/_trim_messages_for_budget).

Выделены в отдельный файл по структуре Фазы 7 (критичный модуль context optimizer).
"""

from src.agent_loop import (
    _estimate_tokens, _message_size, _trim_messages_for_budget, _keep_newest_exchanges,
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
        # кириллица ~2 симв./токен: «Привет мир» = 9 кириллических + пробел
        assert _estimate_tokens("Привет мир") == 4

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
        # Честная оценка токенов (кириллица ~2 симв./токен): 12000 ≈ прежний
        # эффективный реальный контекст при «8000» через len//4.
        assert CONTEXT_TOKEN_BUDGET == 12000


class TestKeepNewestExchanges:
    def test_empty(self):
        assert _keep_newest_exchanges([], 100) == []
        assert _keep_newest_exchanges([], 0) == []

    def test_zero_budget(self):
        assert _keep_newest_exchanges([{"role": "user", "content": "x"}], 0) == []

    def test_keeps_newest_until_budget(self):
        msgs = [{"role": "user", "content": "old"}] * 100
        # каждый user-блок = 2 токена (role + content), бюджет 50 → 25 сообщений
        out = _keep_newest_exchanges(msgs, 50)
        assert out == msgs[-25:]

    def test_assistant_tool_pair_kept_together(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "browser_navigate", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        # Бюджет вмещает только одного — но нельзя оторвать tool от assistant.
        out = _keep_newest_exchanges(msgs, 1000)
        assert len(out) == 2
        assert out[0]["role"] == "assistant" and out[1]["role"] == "tool"

    def test_big_pair_dropped_entirely(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
             "content": "y" * 20000},
            {"role": "tool", "tool_call_id": "c1", "content": "z" * 20000},
        ]
        out = _keep_newest_exchanges(msgs, 100)
        assert out == []


class TestTrimKeepsTaskAnchor:
    def test_keeps_first_user_when_later_user_exists(self):
        """Задача (первое user-сообщение: спека+контекст) не выбрасывается,
        даже если позже появились guidance user-сообщения."""
        task = "x" * 400
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": task},
            {"role": "assistant", "tool_calls": [{"function": {"name": "browser_navigate", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "user", "content": "guidance"},
        ]
        out = _trim_messages_for_budget(messages, budget=3000)
        contents = [m.get("content") for m in out]
        assert contents[0] == "sys"
        assert task in contents  # якорь задачи сохранён
        assert out[-1]["content"] == "guidance"  # последнее user тоже
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= 3000

    def test_context_bounded_when_tail_exceeds_budget(self):
        """Реальный сценарий прогона: одно user-сообщение (спека) + длинная
        история обменов. Итог обязан уложиться в бюджет (старый баг: kept N of N)."""
        messages = [
            {"role": "system", "content": "s" * 100},
            {"role": "user", "content": "Труба полипропиленовая Ду16"},
        ]
        for i in range(60):
            messages.append({"role": "assistant",
                             "tool_calls": [{"function": {"name": "browser_evaluate", "arguments": "{}"}}],
                             "content": ""})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r" * 400})
        out = _trim_messages_for_budget(messages, budget=3000)
        total = sum(_estimate_tokens(m.get("content", "")) for m in out)
        assert total <= 3000
        assert out[0]["role"] == "system"
        assert out[1]["role"] == "user"  # задача на месте
        # Связки не разорваны: tool идёт после assistant с tool_calls
        roles = [m["role"] for m in out]
        assert all(roles[i] != "tool" or (i > 0 and roles[i - 1] == "assistant") for i in range(len(roles)))
