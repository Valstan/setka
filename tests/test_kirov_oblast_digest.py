"""Хелперы и настройки областного дайджеста Кировской области."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.digest_pipeline_settings import POSTOPUS_DIGEST_THEMES
from modules.kirov_oblast_digest import (
    DEFAULT_REGION_CODE,
    OBLAST_LOOKBACK_HOURS,
    THEME_OBLAST,
    _defaults_dict,
    _is_oblast_source_digest_text,
    _is_recent_enough,
    _is_religious_text,
    _resolve_source_region_codes,
)


def test_postopus_themes_includes_oblast():
    assert "oblast" in POSTOPUS_DIGEST_THEMES


def test_constants():
    assert DEFAULT_REGION_CODE == "kirov_obl"
    assert THEME_OBLAST == "oblast"


def test_recent_enough_by_vk_date():
    now = int(time.time())
    fresh = {"date": now - 3600}
    old = {"date": now - int((OBLAST_LOOKBACK_HOURS + 1) * 3600)}
    assert _is_recent_enough(fresh, OBLAST_LOOKBACK_HOURS)
    assert not _is_recent_enough(old, OBLAST_LOOKBACK_HOURS)


def test_oblast_source_digest_text_filters_markers():
    good = "✍ Текст\n[https://vk.com/wall-1_1|Источник]"
    bad = "Реклама\n[https://vk.com/wall-1_1|Источник]"
    assert _is_oblast_source_digest_text(good)
    assert not _is_oblast_source_digest_text(bad)


def test_religious_text_markers():
    assert _is_religious_text("В храме прошла служба")
    assert not _is_religious_text("Открыли новую дорогу в районе")


def test_defaults_dict_empty_and_nested():
    assert _defaults_dict(SimpleNamespace(digest_filters=None)) == {}
    assert _defaults_dict(SimpleNamespace(digest_filters="bad")) == {}
    assert _defaults_dict(
        SimpleNamespace(digest_filters={"defaults": {"oblast_max_wall_refs": 100}})
    ) == {"oblast_max_wall_refs": 100}


@pytest.mark.asyncio
async def test_resolve_source_region_codes_explicit_list_excludes_oblast():
    session = AsyncMock()
    cfg = SimpleNamespace(
        digest_filters={"defaults": {"oblast_source_region_codes": ["mi", "ur", "kirov_obl", "  "]}}
    )
    out = await _resolve_source_region_codes(session, "kirov_obl", cfg)
    assert out == ["mi", "ur"]


@pytest.mark.asyncio
async def test_resolve_source_region_codes_queries_db_when_no_list():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("mi",), ("ur",)]
    session.execute = AsyncMock(return_value=mock_result)
    cfg = SimpleNamespace(digest_filters={"defaults": {}})
    out = await _resolve_source_region_codes(session, "kirov_obl", cfg)
    assert out == ["mi", "ur"]
    assert session.execute.called
