"""Tests for the publications-history endpoint + VK post-id parsing (PR5)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tasks.parsing_scheduler_tasks import _parse_vk_post_id
from web.api import parsing_stats as ps_api


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://vk.com/wall-168170001_3005", 3005),
        ("https://vk.com/wall-1_2", 2),
        ("https://vk.com/wall12345_9", 9),  # owner without minus
        (None, None),
        ("", None),
        ("https://vk.com/club123", None),  # no post id
    ],
)
def test_parse_vk_post_id(url, expected):
    assert _parse_vk_post_id(url) == expected


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        m = MagicMock()
        m.all.return_value = self._items
        return m


class _DB:
    def __init__(self, items):
        self._items = items
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _Result(self._items)


async def test_get_publications_shapes_rows():
    rec = SimpleNamespace(
        id=7,
        region_code="mi",
        theme="novost",
        run_date=datetime(2026, 6, 3, 11, 40),
        posts_final_count=4,
        published_url="https://vk.com/wall-1_2",
        published_post_id=2,
    )
    db = _DB([rec])
    out = await ps_api.get_publications(region_code="mi", theme="novost", days=30, limit=200, db=db)
    assert out["total_records"] == 1
    assert out["period_days"] == 30
    pub = out["publications"][0]
    assert pub == {
        "id": 7,
        "region_code": "mi",
        "theme": "novost",
        "run_date": "2026-06-03T11:40:00",
        "posts_final_count": 4,
        "published_url": "https://vk.com/wall-1_2",
        "published_post_id": 2,
    }


async def test_get_publications_empty():
    db = _DB([])
    out = await ps_api.get_publications(region_code=None, theme=None, days=30, limit=200, db=db)
    assert out["publications"] == []
    assert out["total_records"] == 0
