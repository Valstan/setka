"""Тесты статистики VK-шлюза (web/api/gateway_stats.py) + public-prefix гейта."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from database.models import GatewayRequest
from middleware.auth_gate import PUBLIC_PREFIXES, _is_prefixed
from web.api import gateway_stats as gs


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        m = MagicMock()
        m.all.return_value = self._rows
        return m


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _Result(self._rows)


def _patch_session(rows):
    return patch.object(gs, "AsyncSessionLocal", return_value=_FakeSession(rows))


# --- public-prefix: статистика операторская, шлюз публичный ------------
def test_gateway_endpoints_public_but_stats_not():
    assert _is_prefixed("/api/gateway/call", PUBLIC_PREFIXES) is True
    assert _is_prefixed("/api/gateway/community", PUBLIC_PREFIXES) is True
    # статистика НЕ публична — её закрывает сессионный auth-гейт оператора
    assert _is_prefixed("/api/gateway-stats/summary", PUBLIC_PREFIXES) is False
    assert _is_prefixed("/api/gateway-stats/recent", PUBLIC_PREFIXES) is False


# --- summary -----------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_aggregates_per_project():
    rows = [
        ("GONBA", 5, datetime(2026, 6, 26, 12, 0, 0), 4),
        ("VMALMYZHE", 2, datetime(2026, 6, 25, 9, 0, 0), 2),
    ]
    with _patch_session(rows):
        result = await gs.gateway_stats_summary(days=30)
    assert result["total"] == 7
    gonba = result["projects"][0]
    assert gonba["project"] == "GONBA"
    assert gonba["total"] == 5
    assert gonba["ok"] == 4
    assert gonba["errors"] == 1
    assert gonba["last_used"].startswith("2026-06-26")


@pytest.mark.asyncio
async def test_summary_empty():
    with _patch_session([]):
        result = await gs.gateway_stats_summary(days=7)
    assert result == {"days": 7, "total": 0, "projects": []}


# --- timeline ----------------------------------------------------------
@pytest.mark.asyncio
async def test_timeline_points():
    rows = [("2026-06-25", 3), ("2026-06-26", 8)]
    with _patch_session(rows):
        result = await gs.gateway_stats_timeline(days=30)
    assert result["points"] == [
        {"day": "2026-06-25", "total": 3},
        {"day": "2026-06-26", "total": 8},
    ]


# --- recent ------------------------------------------------------------
@pytest.mark.asyncio
async def test_recent_returns_params():
    row = GatewayRequest(
        project="GONBA",
        endpoint="call",
        method="wall.get",
        params={"owner_id": -1, "count": 3},
        status=200,
        ok=True,
    )
    with _patch_session([row]):
        result = await gs.gateway_stats_recent(limit=50, project="")
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["project"] == "GONBA"
    assert item["method"] == "wall.get"
    assert item["params"] == {"owner_id": -1, "count": 3}  # «что спрашивали» сохранено


# --- usage.record_request best-effort ----------------------------------
@pytest.mark.asyncio
async def test_record_request_swallows_errors():
    """Сбой записи лога не должен пробрасываться (ответ шлюза не ломается)."""
    from modules.gateway import usage

    boom = SimpleNamespace()  # не контекст-менеджер → AsyncSessionLocal() упадёт
    with patch("database.connection.AsyncSessionLocal", return_value=boom):
        # не бросает, несмотря на сломанную сессию
        await usage.record_request("X", "call", "wall.get", {}, status=200, ok=True)
