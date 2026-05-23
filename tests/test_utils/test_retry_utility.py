"""Tests for retry_with_fallback and retry_with_circuit_breaker.

Восстановленные F821-импорты (2026-05-22): `from core.exceptions import
SetkaException` в utils/retry.py пропал при автоматической legacy-зачистке.
Эти функции в runtime никем не вызываются (только в examples/), поэтому
без тестов мы бы пропустили NameError, если импорт снова пропадёт.

Тестируем happy paths + ключевые ветки ошибок.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.exceptions import SetkaException
from utils.retry import CircuitBreaker, retry_with_circuit_breaker, retry_with_fallback

# ---------------------------------------------------------------------------
# retry_with_fallback
# ---------------------------------------------------------------------------


def _named_async_mock(name, **kwargs):
    """AsyncMock с честно проставленным __name__ (нужно retry_with_fallback
    для логов и SetkaException.details)."""
    m = AsyncMock(**kwargs)
    m.__name__ = name
    return m


@pytest.mark.asyncio
async def test_retry_with_fallback_primary_succeeds_first_try():
    """Primary не падает → fallback не вызывается, возвращается primary result."""
    primary = _named_async_mock("primary", return_value="primary_result")
    fallback = _named_async_mock("fallback", return_value="fallback_result")

    result = await retry_with_fallback(primary, fallback, max_attempts=3, arg1="x")

    assert result == "primary_result"
    primary.assert_awaited_once_with(arg1="x")
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_with_fallback_falls_back_after_all_attempts(monkeypatch):
    """Primary падает max_attempts раз → fallback вызывается с теми же args."""
    # Заменим asyncio.sleep — чтобы тест не ждал backoff-секунды.
    import utils.retry as retry_mod

    monkeypatch.setattr(retry_mod.asyncio, "sleep", AsyncMock())

    primary = _named_async_mock("primary", side_effect=RuntimeError("boom"))
    fallback = _named_async_mock("fallback", return_value="fallback_result")

    result = await retry_with_fallback(primary, fallback, max_attempts=2, payload=42)

    assert result == "fallback_result"
    assert primary.await_count == 2
    fallback.assert_awaited_once_with(payload=42)


@pytest.mark.asyncio
async def test_retry_with_fallback_both_fail_raises_setka_exception(monkeypatch):
    """Primary и fallback оба падают → SetkaException с details (имена функций)."""
    import utils.retry as retry_mod

    monkeypatch.setattr(retry_mod.asyncio, "sleep", AsyncMock())

    primary = _named_async_mock("primary", side_effect=RuntimeError("primary_boom"))
    fallback = _named_async_mock("fallback", side_effect=RuntimeError("fallback_boom"))

    with pytest.raises(SetkaException) as exc_info:
        await retry_with_fallback(primary, fallback, max_attempts=1)

    assert exc_info.value.details["primary"] == "primary"
    assert exc_info.value.details["fallback"] == "fallback"
    assert "fallback_boom" in exc_info.value.details["error"]


# ---------------------------------------------------------------------------
# retry_with_circuit_breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_closed_runs_func_and_records_success():
    """CLOSED circuit → func вызывается, success фиксируется."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
    func = AsyncMock(__name__="func", return_value="ok")

    result = await retry_with_circuit_breaker(func, cb, "a", b=1)

    assert result == "ok"
    func.assert_awaited_once_with("a", b=1)
    assert cb.failure_count == 0
    assert cb.state == "CLOSED"


@pytest.mark.asyncio
async def test_circuit_breaker_records_failure_and_reraises():
    """CLOSED circuit + func падает → failure записан, исходное исключение."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
    func = AsyncMock(__name__="func", side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await retry_with_circuit_breaker(func, cb)

    assert cb.failure_count == 1
    assert cb.state == "CLOSED"  # один fail не открывает (threshold=3)


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold_failures():
    """После failure_threshold отказов → state=OPEN, дальше func не вызывается."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
    func = AsyncMock(__name__="func", side_effect=RuntimeError("boom"))

    # 2 fail → OPEN
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await retry_with_circuit_breaker(func, cb)

    assert cb.state == "OPEN"
    assert func.await_count == 2

    # Следующий вызов отбит без обращения к func
    with pytest.raises(SetkaException, match="Circuit breaker is OPEN"):
        await retry_with_circuit_breaker(func, cb)
    assert func.await_count == 2  # без увеличения
