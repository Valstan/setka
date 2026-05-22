"""Tests for VKClient global per-token rate-limit (2026-05-22).

The point is to ensure that multiple VKClient *instances* sharing the same
token honour the same interval — so a parallel-Celery scenario doesn't
burst-call VK and earn captcha cooldown.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from modules.vk_monitor.vk_client import VKClient


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Clear class-level rate-limit registry before/after every test so tests
    don't bleed into each other (especially when run in parallel)."""
    VKClient._last_call_per_token.clear()
    VKClient._per_token_locks.clear()
    yield
    VKClient._last_call_per_token.clear()
    VKClient._per_token_locks.clear()


def _make_client(token="token-A", interval=0.05):
    """Build a VKClient with the vk_api session mocked out."""
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        m.return_value.get_api.return_value = MagicMock()
        c = VKClient(token)
    # Shorten interval to keep CI fast.
    c.GLOBAL_PARSE_INTERVAL_SECONDS = interval
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
    rate-limit. This is the whole point of the class-level registry."""
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
    with patch("modules.vk_monitor.vk_client.vk_api.VkApi") as m:
        session = MagicMock()
        session.method.return_value = {"ok": 1}
        m.return_value = session
        m.return_value.get_api.return_value = MagicMock()
        client = VKClient("token-call")
    client.GLOBAL_PARSE_INTERVAL_SECONDS = interval

    t0 = time.monotonic()
    client.api_call("users.get", {})
    client.api_call("users.get", {})
    elapsed = time.monotonic() - t0

    assert elapsed >= interval * 0.9, f"api_call didn't enforce rate-limit (took {elapsed:.3f}s)"
    assert session.method.call_count == 2
