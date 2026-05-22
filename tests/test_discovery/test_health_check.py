"""Unit tests for modules/discovery/health_check.py — weekly recheck core.

Подход:

- ``VKClient.api_call`` мокаем через MagicMock — возвращаем либо
  ``{"items": [...], "count": N}``, либо ``{"error": {"error_code": …}}``.
- ``categorize_candidate`` патчим в namespace модуля
  ``modules.discovery.health_check`` через monkeypatch — оригинал ходит в
  Groq, нам нужен детерминизм.
- ``Community`` создаём как ORM-объект без сессии (SQLAlchemy позволяет
  такое: row living detached).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from database.models import Community
from modules.discovery import health_check as hc


def _make_community(
    *,
    id_=1,
    vk_id=12345,
    category="novost",
    name="Test community",
    health_status="active",
    last_post_at=None,
    suggested_category=None,
):
    c = Community(
        id=id_,
        region_id=1,
        vk_id=vk_id,
        name=name,
        category=category,
        is_active=True,
    )
    c.health_status = health_status
    c.last_post_at = last_post_at
    c.suggested_category = suggested_category
    return c


def _make_client(api_call_result):
    client = MagicMock()
    if isinstance(api_call_result, list):
        client.api_call.side_effect = api_call_result
    else:
        client.api_call.return_value = api_call_result
    return client


def _post(ts: int, text: str = "пост"):
    return {"id": 1, "date": ts, "text": text}


# ───────── dead-path ─────────


@pytest.mark.asyncio
async def test_dead_on_vk_error_15_access_denied():
    client = _make_client({"error": {"error_code": 15, "error_msg": "Access denied"}})
    community = _make_community()
    res = await hc.check_community_health(client=client, community=community, region_name="Малмыж")
    assert res.status == "dead"
    assert res.error_code == 15
    assert "Access denied" in (res.reasoning or "")


@pytest.mark.asyncio
async def test_dead_on_vk_error_203_group_blocked():
    client = _make_client({"error": {"error_code": 203, "error_msg": "blocked"}})
    res = await hc.check_community_health(
        client=client, community=_make_community(), region_name="Малмыж"
    )
    assert res.status == "dead"
    assert res.error_code == 203


@pytest.mark.asyncio
async def test_transient_error_does_not_change_status():
    # 6 — Too many requests per second (rate limit). Не считаем мёртвым.
    client = _make_client({"error": {"error_code": 6, "error_msg": "rate limit"}})
    community = _make_community(health_status="active")
    res = await hc.check_community_health(client=client, community=community, region_name="Малмыж")
    assert res.status == "active"  # сохраняется существующий статус
    assert res.error_code == 6


# ───────── dormant ─────────


@pytest.mark.asyncio
async def test_dormant_when_wall_is_empty():
    client = _make_client({"items": [], "count": 0})
    res = await hc.check_community_health(
        client=client, community=_make_community(), region_name="Малмыж"
    )
    assert res.status == "dormant"
    assert res.last_post_at is None
    assert res.posts_sampled == 0


@pytest.mark.asyncio
async def test_dormant_when_last_post_older_than_threshold():
    now = datetime(2026, 5, 22, 12, 0, 0)
    # последний пост 100 дней назад
    old_ts = int((now - timedelta(days=100)).timestamp())
    client = _make_client({"items": [_post(old_ts)]})
    res = await hc.check_community_health(
        client=client,
        community=_make_community(),
        region_name="Малмыж",
        dormant_days=60,
        now=now,
    )
    assert res.status == "dormant"
    assert res.last_post_at is not None


@pytest.mark.asyncio
async def test_dormant_threshold_respects_override():
    now = datetime(2026, 5, 22, 12, 0, 0)
    old_ts = int((now - timedelta(days=20)).timestamp())
    client = _make_client({"items": [_post(old_ts)]})
    # 14-day threshold — 20-day-old → dormant
    res = await hc.check_community_health(
        client=client,
        community=_make_community(),
        region_name="X",
        dormant_days=14,
        now=now,
    )
    assert res.status == "dormant"


# ───────── active / changed_category (AI path) ─────────


@pytest.mark.asyncio
async def test_active_when_ai_confirms_same_category(monkeypatch):
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=2)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "сегодняшние новости города")]})

    async def fake_categorize(**_kwargs):
        return {
            "success": True,
            "category": "novost",
            "confidence": 95,
            "is_info_page": False,
            "reasoning": "новостной паблик",
            "model": "test",
        }

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "active"
    assert res.suggested_category is None


@pytest.mark.asyncio
async def test_changed_category_when_ai_drifts_high_confidence(monkeypatch):
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=1)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "продаю гараж недорого")]})

    async def fake_categorize(**_kwargs):
        return {
            "success": True,
            "category": "reklama",
            "confidence": 88,
            "is_info_page": False,
            "reasoning": "теперь это барахолка",
        }

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "changed_category"
    assert res.suggested_category == "reklama"


@pytest.mark.asyncio
async def test_changed_category_skipped_when_confidence_below_threshold(monkeypatch):
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=1)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "что-то непонятное")]})

    async def fake_categorize(**_kwargs):
        return {
            "success": True,
            "category": "reklama",
            "confidence": 50,  # < 70, не доверяем
            "is_info_page": False,
            "reasoning": "may be ads, may be not",
        }

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "active"
    assert res.suggested_category is None


@pytest.mark.asyncio
async def test_changed_category_skipped_for_other(monkeypatch):
    """`other` — escape hatch, не сдвигаем категорию на 'other'."""
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=1)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "что угодно")]})

    async def fake_categorize(**_kwargs):
        return {"success": True, "category": "other", "confidence": 99, "is_info_page": False}

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "active"


@pytest.mark.asyncio
async def test_ai_failure_keeps_active(monkeypatch):
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=1)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "новости")]})

    async def fake_categorize(**_kwargs):
        return {"success": False, "error": "Groq is down"}

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "active"
    assert "Groq is down" in (res.reasoning or "")


@pytest.mark.asyncio
async def test_skips_ai_when_no_text_content(monkeypatch):
    """Wall свежая, но посты без текста — AI не дёргаем (quota saver)."""
    now = datetime(2026, 5, 22, 12, 0, 0)
    fresh_ts = int((now - timedelta(days=1)).timestamp())
    client = _make_client({"items": [_post(fresh_ts, "")]})

    called = {"hit": False}

    async def fake_categorize(**_kwargs):
        called["hit"] = True
        return {"success": True, "category": "reklama", "confidence": 99}

    monkeypatch.setattr(hc, "categorize_candidate", fake_categorize)
    res = await hc.check_community_health(
        client=client,
        community=_make_community(category="novost"),
        region_name="Малмыж",
        now=now,
    )
    assert res.status == "active"
    assert called["hit"] is False


# ───────── edge cases ─────────


@pytest.mark.asyncio
async def test_empty_vk_id_returns_skipped():
    client = MagicMock()
    community = _make_community(vk_id=0)
    res = await hc.check_community_health(client=client, community=community, region_name="X")
    # api_call не должен зваться — мы выходим до этого
    client.api_call.assert_not_called()
    assert res.vk_id == 0
    assert "skipped" in (res.reasoning or "")
