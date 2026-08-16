import asyncio

import pytest

from src.resilience import (
    CircuitBreaker, CircuitBreakerOpenError, CircuitState, MaxRetriesExceeded,
    llm_circuit, mcp_circuit, retry_with_backoff,
)


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.allow_request() is True

    def test_call_success(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.failure_count == 0

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: 1)

    def test_allow_request_false_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_recovery_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=-1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_success_closes_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=-1)
        cb.record_failure()
        cb.allow_request()  # -> HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_record_failure_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_call_async(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

        async def f(x):
            return x * 2

        assert asyncio.run(cb.call_async(f, 21)) == 42


class TestRetryWithBackoff:
    def test_sync_success_first_try(self):
        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def f():
            return "ok"

        assert f() == "ok"

    def test_sync_retries_then_succeeds(self):
        calls = []

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def f():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("refused")
            return "done"

        assert f() == "done"
        assert len(calls) == 2

    def test_sync_exhausts_retries(self):
        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def f():
            raise ConnectionError("refused")

        with pytest.raises(MaxRetriesExceeded):
            f()

    def test_sync_only_catches_specified(self):
        @retry_with_backoff(max_retries=3, base_delay=0.01, exceptions=(TimeoutError,))
        def f():
            raise ValueError("not retried")

        with pytest.raises(ValueError):
            f()

    def test_async_retries_then_succeeds(self):
        calls = []

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        async def f():
            calls.append(1)
            if len(calls) < 2:
                raise TimeoutError("timeout")
            return "done"

        assert asyncio.run(f()) == "done"
        assert len(calls) == 2

    def test_async_exhausts_retries(self):
        @retry_with_backoff(max_retries=2, base_delay=0.01)
        async def f():
            raise ConnectionError("refused")

        with pytest.raises(MaxRetriesExceeded):
            asyncio.run(f())


class TestSingletonCircuits:
    def test_llm_circuit_config(self):
        assert llm_circuit.failure_threshold == 3
        assert llm_circuit.recovery_timeout == 30

    def test_mcp_circuit_config(self):
        assert mcp_circuit.failure_threshold == 5
        assert mcp_circuit.recovery_timeout == 60