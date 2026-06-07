"""Тесты сравнительного графика роста подписчиков (web/api/subscriber_growth).

Чистые хелперы (`build_series`/`summarize_regions`/`_parse_ids`) проверяются
без БД; эндпоинты — с AsyncMock-сессией (в стиле tests/test_api/test_ad_crm.py).
Учёт по ГЛАВНЫМ ИНФО-группам регионов (миграция 033), не по сообществам.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from web.api import subscriber_growth as api

# ----------------------------------------------------------------- helpers


def _rows(items):
    r = MagicMock()
    r.all.return_value = items
    return r


def _db_seq(*results):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    return db


# ----------------------------------------------------------------- build_series


def test_build_series_unifies_axis_and_gaps_to_none():
    rows = [
        (1, date(2026, 6, 1), 100),
        (1, date(2026, 6, 3), 110),  # пропуск 06-02 → None у сообщества 1
        (2, date(2026, 6, 2), 50),
    ]
    out = api.build_series(rows, {1: "Alpha", 2: "Beta"})
    assert out["labels"] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    series = {s["name"]: s["data"] for s in out["series"]}
    assert series["Alpha"] == [100, None, 110]
    assert series["Beta"] == [None, 50, None]


def test_build_series_names_fallback_and_sorted():
    rows = [(7, date(2026, 6, 1), 5), (3, date(2026, 6, 1), 9)]
    out = api.build_series(rows, {3: "Zeta"})  # 7 без имени → fallback
    names = [s["name"] for s in out["series"]]
    assert names == ["Zeta", "регион 7"]  # сортировка по имени (кириллица после латиницы)


def test_build_series_empty():
    out = api.build_series([], {})
    assert out == {"labels": [], "series": []}


# ----------------------------------------------------------- summarize_regions


def test_summarize_delta_and_laggard():
    rows = [
        # растущее
        (1, date(2026, 6, 1), 100),
        (1, date(2026, 6, 2), 120),
        # отстающее (падение, ≥2 точки)
        (2, date(2026, 6, 1), 200),
        (2, date(2026, 6, 2), 190),
        # одна точка — динамики нет, не laggard
        (3, date(2026, 6, 1), 50),
    ]
    meta = {1: {"name": "Up"}, 2: {"name": "Down"}, 3: {"name": "Solo"}}
    out = api.summarize_regions(rows, meta)
    by_id = {c["id"]: c for c in out}

    assert by_id[1]["delta"] == 20
    assert by_id[1]["delta_pct"] == 20.0
    assert by_id[1]["is_laggard"] is False

    assert by_id[2]["delta"] == -10
    assert by_id[2]["is_laggard"] is True

    assert by_id[3]["points"] == 1
    assert by_id[3]["delta"] == 0
    assert by_id[3]["is_laggard"] is False  # одна точка не считается отстающим

    # сортировка: быстрее растущие сверху
    assert [c["id"] for c in out] == [1, 3, 2]


def test_summarize_zero_first_count_no_div_by_zero():
    rows = [(1, date(2026, 6, 1), 0), (1, date(2026, 6, 2), 10)]
    out = api.summarize_regions(rows, {1: {"name": "FromZero"}})
    assert out[0]["delta"] == 10
    assert out[0]["delta_pct"] == 0.0  # деления на ноль нет


def test_summarize_name_fallback():
    out = api.summarize_regions([(9, date(2026, 6, 1), 1)], {})
    assert out[0]["name"] == "регион 9"


# ----------------------------------------------------------------- _parse_ids


def test_parse_ids_csv_and_invalid():
    assert api._parse_ids("1,2,3") == [1, 2, 3]
    assert api._parse_ids(" 4 , x , 5 ,") == [4, 5]
    assert api._parse_ids(None) == []
    assert api._parse_ids("") == []


# ----------------------------------------------------------------- endpoints


@pytest.mark.asyncio
async def test_list_growth_regions_serializes():
    snaps = _rows(
        [
            (1, date(2026, 6, 1), 100),
            (1, date(2026, 6, 2), 130),
        ]
    )
    meta = _rows([(1, "Тужа")])  # (region_id, region_name)
    db = _db_seq(snaps, meta)

    out = await api.list_growth_regions(days=30, db=db)
    assert out["count"] == 1
    assert out["regions"][0]["name"] == "Тужа"
    assert out["regions"][0]["delta"] == 30


@pytest.mark.asyncio
async def test_list_growth_regions_empty():
    db = _db_seq(_rows([]))  # нет снимков → второй запрос не выполняется
    out = await api.list_growth_regions(days=30, db=db)
    assert out["count"] == 0
    assert out["regions"] == []


@pytest.mark.asyncio
async def test_growth_series_no_ids_short_circuits():
    db = AsyncMock()
    db.execute = AsyncMock()
    out = await api.growth_series(ids=None, days=30, db=db)
    assert out == {"days": 30, "labels": [], "series": []}
    db.execute.assert_not_called()  # без id в БД не ходим


@pytest.mark.asyncio
async def test_growth_series_returns_series():
    snaps = _rows([(1, date(2026, 6, 1), 10), (1, date(2026, 6, 2), 12)])
    names = _rows([(1, "Alpha")])
    db = _db_seq(snaps, names)
    out = await api.growth_series(ids="1", days=30, db=db)
    assert out["labels"] == ["2026-06-01", "2026-06-02"]
    assert out["series"][0]["name"] == "Alpha"
    assert out["series"][0]["data"] == [10, 12]
    assert out["days"] == 30
