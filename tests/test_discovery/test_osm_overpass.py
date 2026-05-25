"""Unit tests for modules/discovery/osm_overpass.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from modules.discovery.osm_overpass import _build_query, _extract_names, fetch_localities

# ───────── _build_query ─────────


def test_build_query_includes_district_name():
    q = _build_query("Тужинский район")
    assert "Тужинский район" in q
    assert 'admin_level"~"^[56]$"' in q


def test_build_query_escapes_double_quotes():
    """Double-quotes в name могут разломать Overpass-синтаксис."""
    q = _build_query('Test"District')
    assert 'Test\\"District' in q


def test_build_query_uses_name_ru_and_name():
    q = _build_query("X")
    assert "name:ru" in q
    assert 'name"=' in q


# ───────── _extract_names ─────────


def test_extract_names_prefers_name_ru():
    elements = [
        {"tags": {"name:ru": "Тужа", "name": "Tuzha"}},
    ]
    assert _extract_names(elements) == ["Тужа"]


def test_extract_names_falls_back_to_name():
    elements = [{"tags": {"name": "Шешурга"}}]
    assert _extract_names(elements) == ["Шешурга"]


def test_extract_names_deduplicates_case_insensitive():
    elements = [
        {"tags": {"name": "Тужа"}},
        {"tags": {"name": "ТУЖА"}},
        {"tags": {"name": "тужа"}},
    ]
    assert _extract_names(elements) == ["Тужа"]


def test_extract_names_skips_empty():
    elements = [
        {"tags": {}},
        {"tags": {"name": ""}},
        {"tags": {"name": "   "}},
        {"tags": {"name": "OK"}},
    ]
    assert _extract_names(elements) == ["OK"]


def test_extract_names_returns_sorted():
    elements = [
        {"tags": {"name": "Шешурга"}},
        {"tags": {"name": "Тужа"}},
        {"tags": {"name": "Михайловское"}},
    ]
    assert _extract_names(elements) == ["Михайловское", "Тужа", "Шешурга"]


# ───────── fetch_localities ─────────


def _mock_post_ok(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def test_fetch_localities_happy_path():
    payload = {
        "elements": [
            {"tags": {"name:ru": "Тужа", "place": "town"}},
            {"tags": {"name": "Шешурга", "place": "village"}},
        ]
    }
    with patch(
        "modules.discovery.osm_overpass.requests.post",
        return_value=_mock_post_ok(payload),
    ):
        result = fetch_localities("Тужинский район")
    assert result == ["Тужа", "Шешурга"]


def test_fetch_localities_empty_district_short_circuits():
    """Пустой district_name → не дёргаем requests.post."""
    with patch("modules.discovery.osm_overpass.requests.post") as p:
        assert fetch_localities("") == []
        assert fetch_localities("   ") == []
        p.assert_not_called()


def test_fetch_localities_returns_empty_on_timeout():
    with patch(
        "modules.discovery.osm_overpass.requests.post",
        side_effect=requests.Timeout("OSM took too long"),
    ):
        assert fetch_localities("X") == []


def test_fetch_localities_returns_empty_on_connection_error():
    with patch(
        "modules.discovery.osm_overpass.requests.post",
        side_effect=requests.ConnectionError("DNS"),
    ):
        assert fetch_localities("X") == []


def test_fetch_localities_returns_empty_on_5xx():
    resp = MagicMock()
    resp.status_code = 504
    resp.text = "Gateway timeout"
    with patch("modules.discovery.osm_overpass.requests.post", return_value=resp):
        assert fetch_localities("X") == []


def test_fetch_localities_returns_empty_on_bad_json():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    with patch("modules.discovery.osm_overpass.requests.post", return_value=resp):
        assert fetch_localities("X") == []


def test_fetch_localities_returns_empty_on_no_elements():
    """Запрос на несуществующий район — OSM вернёт {elements: []}."""
    with patch(
        "modules.discovery.osm_overpass.requests.post",
        return_value=_mock_post_ok({"elements": []}),
    ):
        assert fetch_localities("Несуществующий район") == []
