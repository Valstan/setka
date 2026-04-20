"""Хелперы и настройки областного дайджеста Кировской области."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.kirov_oblast_digest import (
    DEFAULT_REGION_CODE,
    THEME_OBLAST,
    _defaults_dict,
    _resolve_source_region_codes,
)
from modules.digest_pipeline_settings import POSTOPUS_DIGEST_THEMES


def test_postopus_themes_includes_oblast():
    assert "oblast" in POSTOPUS_DIGEST_THEMES


def test_constants():
    assert DEFAULT_REGION_CODE == "kirov_obl"
    assert THEME_OBLAST == "oblast"


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
        digest_filters={
            "defaults": {"oblast_source_region_codes": ["mi", "ur", "kirov_obl", "  "]}
        }
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
