"""Тесты idle-таймаута строки (_run_row_with_idle_timeout).

Строка НЕ режется по «стенам»: пока агент жив (вызывает колбэки/меняет
активность), она работает сколько нужно. Отменяется только при зависании
(activity['last'] не менялся дольше idle_timeout) и при жёстком пределе.
"""

import asyncio
import time

import pytest

from src.mcp_agent_runner import _run_row_with_idle_timeout


async def _noop_coro(result=None, *, delay=0.0):
    if delay:
        await asyncio.sleep(delay)
    return result


@pytest.mark.asyncio
async def test_completes_without_activity():
    """Корутина, завершающаяся быстрее idle_timeout, возвращается как есть."""
    activity = {"last": time.monotonic()}
    out = await _run_row_with_idle_timeout(
        lambda: _noop_coro({"price": 100}, delay=0.05),
        idle_timeout=5.0, max_seconds=10.0, activity=activity,
    )
    assert out == {"price": 100}


@pytest.mark.asyncio
async def test_idle_timeout_cancels_stuck_row():
    """Агент молчит дольше idle_timeout (нет признаков жизни) → TimeoutError."""
    activity = {"last": time.monotonic()}
    with pytest.raises(asyncio.TimeoutError):
        await _run_row_with_idle_timeout(
            lambda: _noop_coro(delay=30.0),
            idle_timeout=0.1, max_seconds=30.0, activity=activity,
        )


@pytest.mark.asyncio
async def test_activity_keeps_row_alive():
    """Агент делает шаги дольше idle_timeout, но активность обновляется —
    строка НЕ отменяется (регрессия: 300с «по стенам» убивал строку №2)."""
    activity = {"last": time.monotonic()}

    async def slow_but_alive():
        for _ in range(6):
            activity["last"] = time.monotonic()  # признак жизни
            await asyncio.sleep(0.05)
        return {"price": 3580}

    out = await _run_row_with_idle_timeout(
        slow_but_alive, idle_timeout=0.08, max_seconds=5.0, activity=activity,
    )
    assert out == {"price": 3580}


@pytest.mark.asyncio
async def test_max_seconds_hard_cap():
    """Жёсткий предел max_seconds срабатывает даже при непрерывной активности."""
    activity = {"last": time.monotonic()}

    async def forever_alive():
        while True:
            activity["last"] = time.monotonic()
            await asyncio.sleep(0.01)

    with pytest.raises(asyncio.TimeoutError):
        await _run_row_with_idle_timeout(
            forever_alive, idle_timeout=30.0, max_seconds=0.15, activity=activity,
        )


@pytest.mark.asyncio
async def test_cancelled_propagates():
    """Отмена пользователем пробрасывается как CancelledError, не маскируется."""
    activity = {"last": time.monotonic()}

    async def user_stop():
        raise asyncio.CancelledError("stopped by user")

    with pytest.raises(asyncio.CancelledError):
        await _run_row_with_idle_timeout(
            user_stop, idle_timeout=5.0, max_seconds=10.0, activity=activity,
        )
