"""Тесты гео-модуля: ``geocode`` (Nominatim, мок сети) + ``haversine_km``.

Сеть не трогаем — ``httpx.AsyncClient`` подменяется фейком (как и в остальных
unit-тестах проекта, где внешние сервисы мокаются, а не вызываются по-настоящему).
"""

from __future__ import annotations

import httpx
import pytest

from modules.geo import geocoder


class _FakeResp:
    def __init__(self, data, *, ok: bool = True):
        self._data = data
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._data


class _FakeClient:
    """Мок httpx.AsyncClient: async-context-manager с настраиваемым .get()."""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.last_params = params
        if self._exc is not None:
            raise self._exc
        return self._resp


def _patch_client(monkeypatch, *, resp=None, exc=None):
    client = _FakeClient(resp=resp, exc=exc)
    monkeypatch.setattr(geocoder.httpx, "AsyncClient", lambda *a, **k: client)
    return client


# ---------------------------------------------------------------------------
# geocode
# ---------------------------------------------------------------------------


async def test_geocode_returns_coords(monkeypatch):
    client = _patch_client(monkeypatch, resp=_FakeResp([{"lat": "57.0928", "lon": "50.0594"}]))
    coords = await geocoder.geocode("Тужа")
    assert coords == (57.0928, 50.0594)
    # country_bias добавлен в запрос
    assert "Россия" in client.last_params["q"]


async def test_geocode_does_not_duplicate_country_bias(monkeypatch):
    client = _patch_client(monkeypatch, resp=_FakeResp([{"lat": "1", "lon": "2"}]))
    await geocoder.geocode("Малмыж, Россия")
    assert client.last_params["q"].count("Россия") == 1


async def test_geocode_appends_region_hint(monkeypatch):
    # region_hint дизамбигуирует омонимы (Советск Кировский vs Калининградский).
    client = _patch_client(monkeypatch, resp=_FakeResp([{"lat": "1", "lon": "2"}]))
    await geocoder.geocode("Советск", region_hint="Кировская область")
    q = client.last_params["q"]
    assert "Кировская область" in q and "Россия" in q


async def test_geocode_skips_hint_already_present(monkeypatch):
    client = _patch_client(monkeypatch, resp=_FakeResp([{"lat": "1", "lon": "2"}]))
    await geocoder.geocode("Советск, Кировская область", region_hint="Кировская область")
    assert client.last_params["q"].count("Кировская область") == 1


async def test_geocode_empty_label_returns_none(monkeypatch):
    # Даже не должен ходить в сеть — но на всякий случай мок отдаёт пусто.
    _patch_client(monkeypatch, resp=_FakeResp([]))
    assert await geocoder.geocode("") is None
    assert await geocoder.geocode("   ") is None


async def test_geocode_no_result_returns_none(monkeypatch):
    _patch_client(monkeypatch, resp=_FakeResp([]))
    assert await geocoder.geocode("Несуществующийгород") is None


async def test_geocode_http_error_returns_none(monkeypatch):
    _patch_client(monkeypatch, exc=httpx.ConnectError("down"))
    assert await geocoder.geocode("Тужа") is None


async def test_geocode_bad_payload_returns_none(monkeypatch):
    _patch_client(monkeypatch, resp=_FakeResp([{"no_lat": "x"}]))
    assert await geocoder.geocode("Тужа") is None


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------


def test_haversine_zero_for_same_point():
    assert geocoder.haversine_km((57.0, 50.0), (57.0, 50.0)) == pytest.approx(0.0, abs=1e-6)


def test_haversine_one_degree_latitude_is_about_111km():
    # 1° широты ≈ 111.19 км.
    d = geocoder.haversine_km((0.0, 0.0), (1.0, 0.0))
    assert d == pytest.approx(111.19, abs=0.5)


def test_haversine_known_pair_kirov_kazan():
    # Киров (58.60, 49.66) — Казань (55.79, 49.12): ~313 км по большому кругу.
    d = geocoder.haversine_km((58.6035, 49.6679), (55.7963, 49.1088))
    assert d == pytest.approx(313, abs=15)
