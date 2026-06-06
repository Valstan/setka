"""Tests for VKClient global per-token rate-limit.

Базовые сценарии (per-process поведение через ThreadingRateLimiter):

- Первый вызов не спит.
- Два back-to-back на одном токене разнесены интервалом.
- Два VKClient инстанса с одним токеном делят лимит.
- Разные токены не блокируют друг друга.
- Concurrent threads сериализуются.
- ``api_call()`` дёргает rate-limit.

Дополнительно (refactor 2026-05-26):

- ``build_rate_limiter()`` возвращает threading-backend по дефолту.
- RedisRateLimiter формирует ожидаемый Redis-ключ + дёргает Lua-script.
- При недоступном Redis — graceful fallback на ThreadingRateLimiter.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from modules.vk_monitor.rate_limiter import (
    REDIS_KEY_PREFIX,
    RedisRateLimiter,
    ThreadingRateLimiter,
    build_rate_limiter,
)
from modules.vk_monitor.vk_client import VKClient


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Drop the shared limiter so each test rebuilds it with whatever
    GLOBAL_PARSE_INTERVAL_SECONDS it set."""
    VKClient._rate_limiter = None
    yield
    VKClient._rate_limiter = None


def _make_client(token="token-A", interval=0.05):
    """Build a VKClient with the vk_api session mocked out and class-level
    interval lowered so the test stays fast."""
    VKClient.GLOBAL_PARSE_INTERVAL_SECONDS = interval
    VKClient._rate_limiter = None  # force rebuild with new interval
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        m.return_value.get_api.return_value = MagicMock()
        c = VKClient(token)
    return c


def test_single_call_does_not_sleep():
    """First call ever to a token must not block (no prior state)."""
    client = _make_client()
    t0 = time.monotonic()
    client._enforce_rate_limit()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.02, f"first call shouldn't sleep, got {elapsed:.3f}s"


def test_two_back_to_back_calls_are_spaced():
    """Second call within the interval must wait until the interval elapses."""
    interval = 0.1
    client = _make_client(interval=interval)

    t0 = time.monotonic()
    client._enforce_rate_limit()
    client._enforce_rate_limit()
    elapsed = time.monotonic() - t0

    # The 2nd call must have slept ~interval. Allow generous jitter on Windows.
    assert elapsed >= interval * 0.9, f"expected >= {interval * 0.9:.3f}s, got {elapsed:.3f}s"
    assert elapsed < interval * 3, f"unexpectedly slow: {elapsed:.3f}s"


def test_two_instances_share_per_token_limit():
    """Two different VKClient instances on the SAME token still share the
    rate-limit. This is the whole point of the shared limiter."""
    interval = 0.1
    c1 = _make_client(token="shared", interval=interval)
    c2 = _make_client(token="shared", interval=interval)

    t0 = time.monotonic()
    c1._enforce_rate_limit()
    c2._enforce_rate_limit()  # second instance, same token → must wait
    elapsed = time.monotonic() - t0

    assert (
        elapsed >= interval * 0.9
    ), f"two instances on same token didn't share limit (took {elapsed:.3f}s)"


def test_different_tokens_do_not_block_each_other():
    """Two clients on DIFFERENT tokens should not delay each other —
    rate-limit is per-token, not global."""
    interval = 0.2
    c_a = _make_client(token="token-A", interval=interval)
    c_b = _make_client(token="token-B", interval=interval)

    t0 = time.monotonic()
    c_a._enforce_rate_limit()
    c_b._enforce_rate_limit()  # different token, should pass immediately
    elapsed = time.monotonic() - t0

    assert elapsed < interval * 0.5, f"different tokens unexpectedly blocked (took {elapsed:.3f}s)"


def test_thread_safety_no_double_spend():
    """Concurrent threads on the same token must serialise — total time
    for N calls is at least (N-1) * interval, not less."""
    interval = 0.08
    n_calls = 4
    client = _make_client(token="concurrent", interval=interval)

    errors = []

    def hammer():
        try:
            client._enforce_rate_limit()
        except Exception as e:  # pragma: no cover — sanity
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(n_calls)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.monotonic() - t0

    assert not errors, errors
    expected_min = (n_calls - 1) * interval * 0.9  # allow 10% slack
    assert elapsed >= expected_min, (
        f"concurrent calls weren't serialised — {n_calls} calls took {elapsed:.3f}s, "
        f"expected >= {expected_min:.3f}s"
    )


def test_api_call_invokes_rate_limit():
    """Synchronous api_call() must enforce the limit before hitting vk_api."""
    interval = 0.1
    VKClient.GLOBAL_PARSE_INTERVAL_SECONDS = interval
    VKClient._rate_limiter = None
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        session = MagicMock()
        session.method.return_value = {"ok": 1}
        m.return_value = session
        m.return_value.get_api.return_value = MagicMock()
        client = VKClient("token-call")

    t0 = time.monotonic()
    client.api_call("users.get", {})
    client.api_call("users.get", {})
    elapsed = time.monotonic() - t0

    assert elapsed >= interval * 0.9, f"api_call didn't enforce rate-limit (took {elapsed:.3f}s)"
    assert session.method.call_count == 2


# =====================================================================
# build_rate_limiter() — backend selection
# =====================================================================


def test_build_rate_limiter_threading_default(monkeypatch):
    """No env var → ThreadingRateLimiter."""
    monkeypatch.delenv("VK_RATE_LIMIT_BACKEND", raising=False)
    limiter = build_rate_limiter(interval=0.1)
    assert isinstance(limiter, ThreadingRateLimiter)
    assert limiter.interval == pytest.approx(0.1)


def test_build_rate_limiter_explicit_threading(monkeypatch):
    monkeypatch.setenv("VK_RATE_LIMIT_BACKEND", "threading")
    limiter = build_rate_limiter(interval=0.1)
    assert isinstance(limiter, ThreadingRateLimiter)


def test_build_rate_limiter_redis_unavailable_falls_back(monkeypatch):
    """``VK_RATE_LIMIT_BACKEND=redis`` без рабочего Redis → ThreadingRateLimiter,
    система не падает."""
    monkeypatch.setenv("VK_RATE_LIMIT_BACKEND", "redis")
    # Подменим _build_redis_client чтобы он вернул None (нет коннекта).
    with patch("modules.vk_monitor.rate_limiter._build_redis_client", return_value=None):
        limiter = build_rate_limiter(interval=0.1)
    assert isinstance(limiter, ThreadingRateLimiter)


def test_build_rate_limiter_redis_when_available(monkeypatch):
    """``VK_RATE_LIMIT_BACKEND=redis`` + рабочий Redis → RedisRateLimiter."""
    monkeypatch.setenv("VK_RATE_LIMIT_BACKEND", "redis")
    fake_client = MagicMock()
    fake_client.register_script.return_value = MagicMock(return_value=0)
    with patch("modules.vk_monitor.rate_limiter._build_redis_client", return_value=fake_client):
        limiter = build_rate_limiter(interval=0.25)
    assert isinstance(limiter, RedisRateLimiter)
    assert limiter.interval == pytest.approx(0.25)
    assert limiter.interval_ms == 250


# =====================================================================
# RedisRateLimiter — Lua-script invocation
# =====================================================================


def test_redis_rate_limiter_key_format():
    """Ключ Redis = ``setka:vk_ratelimit:<sha256[:16]>`` — токен не хранится сырым."""
    fake_client = MagicMock()
    fake_client.register_script.return_value = MagicMock(return_value=0)
    limiter = RedisRateLimiter(fake_client, interval=0.1)

    key = limiter._key("my-secret-token")
    assert key.startswith(f"{REDIS_KEY_PREFIX}:")
    assert "my-secret-token" not in key
    # Determinism: same token → same key.
    assert key == limiter._key("my-secret-token")


def test_redis_rate_limiter_wait_no_sleep():
    """Lua-script returns 0 → wait() не спит."""
    fake_client = MagicMock()
    script = MagicMock(return_value=0)
    fake_client.register_script.return_value = script
    limiter = RedisRateLimiter(fake_client, interval=0.1)

    t0 = time.monotonic()
    limiter.wait("token-X")
    elapsed = time.monotonic() - t0

    assert elapsed < 0.02, f"should not sleep on wait_ms=0 (got {elapsed:.3f}s)"
    script.assert_called_once()
    call_kwargs = script.call_args.kwargs
    assert call_kwargs["keys"][0].startswith(REDIS_KEY_PREFIX)
    # args: [now_ms, interval_ms]
    assert call_kwargs["args"][1] == 100  # interval_ms = 0.1 * 1000


def test_redis_rate_limiter_wait_sleeps_returned_amount():
    """Lua-script returns wait_ms → wait() спит примерно столько же.

    Используем большой interval (200ms), чтобы на Windows с 15ms timer
    resolution всё ещё надёжно отличить «спал» от «не спал»."""
    wait_ms = 200
    fake_client = MagicMock()
    script = MagicMock(return_value=wait_ms)
    fake_client.register_script.return_value = script
    limiter = RedisRateLimiter(fake_client, interval=0.5)

    t0 = time.monotonic()
    limiter.wait("token-X")
    elapsed = time.monotonic() - t0

    # 30% slack на Windows timer jitter и CI shared runners.
    assert (
        elapsed >= wait_ms / 1000.0 * 0.7
    ), f"expected >= {wait_ms * 0.7:.0f}ms, got {elapsed * 1000:.1f}ms"
    assert elapsed < 0.5, f"unexpectedly slow: {elapsed * 1000:.1f}ms"


def test_redis_rate_limiter_lua_failure_does_not_crash():
    """Если Lua-call упал (Redis down mid-call), wait() логирует и возвращается
    без блокировки — приоритет «не повесить прод»."""
    fake_client = MagicMock()
    script = MagicMock(side_effect=ConnectionError("redis down"))
    fake_client.register_script.return_value = script
    limiter = RedisRateLimiter(fake_client, interval=0.1)

    # Should NOT raise.
    t0 = time.monotonic()
    limiter.wait("token-X")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.02
