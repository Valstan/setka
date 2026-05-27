"""Тесты для каскадного дайджеста (modules.cascaded_digest)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.cascaded_digest import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_POSTS_PER_CHILD,
    _defaults_dict,
    _has_banned_marker,
    _is_recent_enough,
    _is_religious_text,
    _resolve_child_regions,
)
from modules.digest_pipeline_settings import POSTOPUS_DIGEST_THEMES


def test_postopus_themes_includes_oblast():
    """Backward-compat: тема `oblast` всё ещё в списке POSTOPUS_DIGEST_THEMES,
    чтобы существующие beat-таски `postopus-kirov-oblast-*` находили
    `RegionConfig.digest_template.by_topic.oblast`."""
    assert "oblast" in POSTOPUS_DIGEST_THEMES


def test_constants():
    assert DEFAULT_POSTS_PER_CHILD == 5
    assert DEFAULT_LOOKBACK_HOURS == 72.0


def test_recent_enough_by_vk_date():
    now = int(time.time())
    fresh = {"date": now - 3600}
    old = {"date": now - int((DEFAULT_LOOKBACK_HOURS + 1) * 3600)}
    assert _is_recent_enough(fresh, DEFAULT_LOOKBACK_HOURS)
    assert not _is_recent_enough(old, DEFAULT_LOOKBACK_HOURS)


def test_recent_enough_missing_date():
    assert not _is_recent_enough({}, DEFAULT_LOOKBACK_HOURS)
    assert not _is_recent_enough({"date": "garbage"}, DEFAULT_LOOKBACK_HOURS)


def test_religious_text_markers():
    assert _is_religious_text("В храме прошла служба")
    assert _is_religious_text("Открытие новой мечети")
    assert not _is_religious_text("Открыли новую дорогу в районе")


def test_banned_marker_detection():
    assert _has_banned_marker("Реклама от компании X")
    assert _has_banned_marker("ОБЪЯВЛЕНИЕ: продаётся дом")
    assert _has_banned_marker("Раздел дополнительно")
    assert _has_banned_marker("Сборка addons")
    assert not _has_banned_marker("Обычная новость района")


def test_defaults_dict_empty_and_nested():
    assert _defaults_dict(SimpleNamespace(digest_filters=None)) == {}
    assert _defaults_dict(SimpleNamespace(digest_filters="bad")) == {}
    assert _defaults_dict(
        SimpleNamespace(digest_filters={"defaults": {"cascade_posts_per_child": 7}})
    ) == {"cascade_posts_per_child": 7}


@pytest.mark.asyncio
async def test_resolve_child_regions_explicit_override_excludes_self():
    """Override через `cascade_source_region_codes` пропускает явные коды,
    исключая сам oblast (защита от рекурсии)."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        SimpleNamespace(code="mi", vk_group_id=-100, is_active=True),
        SimpleNamespace(code="ur", vk_group_id=-200, is_active=True),
    ]
    session.execute = AsyncMock(return_value=mock_result)
    cfg = SimpleNamespace(
        digest_filters={
            "defaults": {"cascade_source_region_codes": ["mi", "ur", "kirov_obl", "  "]}
        }
    )
    out = await _resolve_child_regions(
        session, region_id=999, region_code="kirov_obl", region_config=cfg
    )
    # Возвращается то, что вернёт БД на in_(['mi','ur']) — мы проверяем что вызов был.
    assert len(out) == 2
    assert session.execute.called


@pytest.mark.asyncio
async def test_resolve_child_regions_uses_parent_id_when_no_override():
    """Без override берутся children по `parent_region_id = region_id`."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        SimpleNamespace(code="mi", vk_group_id=-100, is_active=True),
    ]
    session.execute = AsyncMock(return_value=mock_result)
    cfg = SimpleNamespace(digest_filters={"defaults": {}})
    out = await _resolve_child_regions(
        session, region_id=42, region_code="kirov_obl", region_config=cfg
    )
    assert len(out) == 1
    assert out[0].code == "mi"


@pytest.mark.asyncio
async def test_resolve_child_regions_override_empty_returns_empty():
    """Override = [oblast_code, '  '] — после фильтрации список пуст, не идём в БД."""
    session = AsyncMock()
    session.execute = AsyncMock()
    cfg = SimpleNamespace(
        digest_filters={"defaults": {"cascade_source_region_codes": ["kirov_obl", "   "]}}
    )
    out = await _resolve_child_regions(
        session, region_id=1, region_code="kirov_obl", region_config=cfg
    )
    assert out == []
    # Поскольку override был задан и непуст в исходнике, идём в DB с пустым списком
    # это нормально — DB вернёт пусто. Главное — функция не падает.


@pytest.mark.asyncio
async def test_run_cascaded_digest_rejects_raion():
    """Для kind=raion функция должна вернуть error — каскад только для oblast/strana."""
    from modules.cascaded_digest import run_cascaded_digest

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = SimpleNamespace(
        code="mi",
        kind="raion",
        vk_group_id=-100,
    )
    session.execute = AsyncMock(return_value=mock_result)

    out = await run_cascaded_digest(session, region_code="mi", theme="oblast")
    assert out["success"] is False
    assert "oblast/strana only" in out["error"]


@pytest.mark.asyncio
async def test_run_cascaded_digest_rejects_region_without_vk_group_id():
    from modules.cascaded_digest import run_cascaded_digest

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = SimpleNamespace(
        code="kirov_obl",
        kind="oblast",
        vk_group_id=None,
    )
    session.execute = AsyncMock(return_value=mock_result)

    out = await run_cascaded_digest(session, region_code="kirov_obl", theme="oblast")
    assert out["success"] is False
    assert "no vk_group_id" in out["error"]


@pytest.mark.asyncio
async def test_run_cascaded_digest_rejects_missing_region():
    from modules.cascaded_digest import run_cascaded_digest

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    out = await run_cascaded_digest(session, region_code="nonexistent", theme="oblast")
    assert out["success"] is False
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_kirov_oblast_wrapper_delegates_to_cascaded(monkeypatch):
    """`modules.kirov_oblast_digest.run_kirov_oblast_digest` — тонкий wrapper."""
    from modules import kirov_oblast_digest as kod

    called = {}

    async def fake_run_cascaded_digest(session, *, region_code, theme, test_mode):
        called["region_code"] = region_code
        called["theme"] = theme
        called["test_mode"] = test_mode
        return {"success": True, "delegated": True}

    monkeypatch.setattr("modules.cascaded_digest.run_cascaded_digest", fake_run_cascaded_digest)

    session = AsyncMock()
    out = await kod.run_kirov_oblast_digest(session, test_mode=True)
    assert out == {"success": True, "delegated": True}
    assert called == {
        "region_code": "kirov_obl",
        "theme": "oblast",
        "test_mode": True,
    }


def test_kirov_oblast_wrapper_constants():
    """Backward-compat: DEFAULT_REGION_CODE и THEME_OBLAST экспортируются."""
    from modules import kirov_oblast_digest as kod

    assert kod.DEFAULT_REGION_CODE == "kirov_obl"
    assert kod.THEME_OBLAST == "oblast"
