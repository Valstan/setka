"""Тесты гео-подсказки соседей: ``_geocodable_label`` + endpoint ``suggest_neighbors``.

Сессия БД и геокодер мокаются (``AsyncMock``) — без реальной БД и сети, в стиле
``tests/test_api/test_neighbors_bidirectional.py`` (урок PR1: unit-тесты проекта
не поднимают SQLite, мокаем сессию). Покрываем:

* ``_geocodable_label`` — вывод метки из ``center_city`` / ``name`` (стрип «- ИНФО»,
  гео-хвоста, приоритет center_city);
* endpoint — ранжирование по дистанции, флаг ``within_threshold``, ``not_geocoded``,
  исключение себя и служебного ``test``, режимы code/label, 400 без аргументов.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from web.api import regions as regions_api

# ---------------------------------------------------------------------------
# _geocodable_label
# ---------------------------------------------------------------------------


def test_label_prefers_center_city():
    assert regions_api._geocodable_label("МАЛМЫЖ - ИНФО", "Малмыж") == "Малмыж"


def test_label_strips_center_city_geo_tail():
    assert regions_api._geocodable_label("X", "Малмыж, Кировская область") == "Малмыж"


def test_label_strips_info_suffix_from_name():
    assert regions_api._geocodable_label("МАЛМЫЖ - ИНФО", None) == "МАЛМЫЖ"


def test_label_strips_geo_tail_from_name():
    assert regions_api._geocodable_label("Тужа, Кировская область", None) == "Тужа"


def test_label_handles_unicode_dashes():
    for dash in ("-", "–", "—"):
        assert regions_api._geocodable_label(f"СОВЕТСК {dash} ИНФО", None) == "СОВЕТСК"


def test_label_empty_returns_none():
    assert regions_api._geocodable_label(None, None) is None
    assert regions_api._geocodable_label("", "") is None


# ---------------------------------------------------------------------------
# suggest_neighbors endpoint
# ---------------------------------------------------------------------------


def _result_scalar_one(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _result_scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _region(code, name, *, kind="raion", geo=None, center_city=None):
    config = {"geo": geo} if geo else {}
    return SimpleNamespace(code=code, name=name, kind=kind, center_city=center_city, config=config)


async def test_suggest_label_mode_ranks_and_flags(monkeypatch):
    # Цель геокодится в (57.0, 50.0); кандидаты — близкий, далёкий, без гео, test.
    monkeypatch.setattr(regions_api, "geocode", AsyncMock(return_value=(57.0, 50.0)))
    near = _region("near", "Близкий", geo={"lat": 57.1, "lon": 50.1})
    far = _region("far", "Далёкий", geo={"lat": 52.0, "lon": 45.0})
    nogeo = _region("nogeo", "Без координат")
    test = _region("test", "Служебный", geo={"lat": 57.0, "lon": 50.0})

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalars_all([near, far, nogeo, test]))

    resp = await regions_api.suggest_neighbors(
        code=None, label="Тужа", kind="raion", max_km=90.0, db=session
    )

    assert resp["target"]["geocoded"] is True
    codes = [s["code"] for s in resp["suggestions"]]
    assert codes == ["near", "far"]  # отсортировано по дистанции; test исключён
    near_s = resp["suggestions"][0]
    far_s = resp["suggestions"][1]
    assert near_s["within_threshold"] is True
    assert far_s["within_threshold"] is False
    assert resp["not_geocoded"] == ["nogeo"]


async def test_suggest_code_mode_excludes_self(monkeypatch):
    # Цель «a» с закэшированными координатами — geocode дёргаться не должен.
    geocode_mock = AsyncMock(return_value=(0.0, 0.0))
    monkeypatch.setattr(regions_api, "geocode", geocode_mock)
    target = _region("a", "Альфа", geo={"lat": 57.0, "lon": 50.0})
    other = _region("b", "Бета", geo={"lat": 57.1, "lon": 50.1})

    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_result_scalar_one(target), _result_scalars_all([target, other])]
    )

    resp = await regions_api.suggest_neighbors(
        code="a", label=None, kind="raion", max_km=90.0, db=session
    )

    assert resp["target"]["geocoded"] is True
    assert [s["code"] for s in resp["suggestions"]] == ["b"]  # сам «a» исключён
    geocode_mock.assert_not_awaited()  # координаты были в кэше


async def test_suggest_target_not_geocodable_returns_empty(monkeypatch):
    monkeypatch.setattr(regions_api, "geocode", AsyncMock(return_value=None))
    session = AsyncMock()
    resp = await regions_api.suggest_neighbors(
        code=None, label="Несуществующий", kind="raion", max_km=90.0, db=session
    )
    assert resp["target"]["geocoded"] is False
    assert resp["suggestions"] == []
    assert resp["not_geocoded"] == []


async def test_suggest_requires_code_or_label():
    session = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await regions_api.suggest_neighbors(
            code=None, label=None, kind="raion", max_km=90.0, db=session
        )
    assert exc.value.status_code == 400


async def test_suggest_code_mode_404_for_unknown():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(None))
    with pytest.raises(HTTPException) as exc:
        await regions_api.suggest_neighbors(
            code="ghost", label=None, kind="raion", max_km=90.0, db=session
        )
    assert exc.value.status_code == 404
