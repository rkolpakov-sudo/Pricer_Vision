import asyncio
import inspect
import logging
import random
import time
from enum import Enum
from functools import wraps

logger = logging.getLogger("pricer.resilience")


class CircuitState(Enum):
    CLOSED = "closed"       # Нормальная работа
    OPEN = "open"           # Отказ, запросы блокируются
    HALF_OPEN = "half_open" # Проверка восстановления


class CircuitBreakerOpenError(Exception):
    pass


class MaxRetriesExceeded(Exception):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60,
                 expected_exception=Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    def allow_request(self) -> bool:
        """Проверить, можно ли выполнять запрос. При прошедшем recovery_timeout
        переводит OPEN -> HALF_OPEN и пропускает пробный запрос."""
        if self.state == CircuitState.OPEN:
            if time.time() - (self.last_failure_time or 0) > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker: attempting recovery")
                return True
            return False
        return True

    def call(self, func, *args, **kwargs):
        if not self.allow_request():
            raise CircuitBreakerOpenError(
                f"Service unavailable. Retry after {self.recovery_timeout}s"
            )
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exception as e:
            self.record_failure()
            raise

    async def call_async(self, func, *args, **kwargs):
        if not self.allow_request():
            raise CircuitBreakerOpenError(
                f"Service unavailable. Retry after {self.recovery_timeout}s"
            )
        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exception as e:
            self.record_failure()
            raise

    def record_success(self):
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logger.info("Circuit breaker: service recovered")

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error("Circuit breaker OPEN: %d failures", self.failure_count)

    def reset(self):
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED


# Готовые инстансы
llm_circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
mcp_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=60)


def retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0,
                       exceptions=(Exception,)):
    """Экспоненциальная задержка с джиттером. Работает с sync и async функциями."""
    def decorator(func):
        is_coro = inspect.iscoroutinefunction(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            if is_coro:
                return _async_retry(func, max_retries, base_delay, max_delay,
                                    exceptions, *args, **kwargs)
            return _sync_retry(func, max_retries, base_delay, max_delay,
                               exceptions, *args, **kwargs)
        return wrapper
    return decorator


def _retry_loop(max_retries, base_delay, max_delay, exceptions, exc, attempt):
    if attempt == max_retries - 1:
        raise MaxRetriesExceeded(f"Failed after {max_retries} attempts: {exc}") from exc
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.uniform(0, delay * 0.1)
    wait_time = delay + jitter
    logger.warning(
        "Attempt %d failed: %s. Retrying in %.1fs...", attempt + 1, exc, wait_time
    )
    return wait_time


def _sync_retry(func, max_retries, base_delay, max_delay, exceptions,
                *args, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            wait_time = _retry_loop(max_retries, base_delay, max_delay,
                                    exceptions, e, attempt)
            time.sleep(wait_time)
    raise MaxRetriesExceeded(f"Failed after {max_retries} attempts")


async def _async_retry(func, max_retries, base_delay, max_delay, exceptions,
                       *args, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            wait_time = _retry_loop(max_retries, base_delay, max_delay,
                                    exceptions, e, attempt)
            await asyncio.sleep(wait_time)
    raise MaxRetriesExceeded(f"Failed after {max_retries} attempts")


# Использование:
# @retry_with_backoff(max_retries=3, exceptions=(TimeoutError, ConnectionError))
# async def navigate_to_site(url):
#     return await mcp_bridge.call_tool("browser_navigate", {"url": url})