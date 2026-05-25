"""Unit tests for `_read_region_discovery_config` — парс region.config."""

from __future__ import annotations

from types import SimpleNamespace

from tasks.discovery_tasks import _read_region_discovery_config


def _region(config):
    return SimpleNamespace(config=config)


def test_config_none_returns_empty_lists():
    loc, kw = _read_region_discovery_config(_region(None))
    assert loc == []
    assert kw == []


def test_config_empty_dict_returns_empty_lists():
    loc, kw = _read_region_discovery_config(_region({}))
    assert loc == []
    assert kw == []


def test_config_list_format():
    cfg = {"localities": ["Тужа", "Шешурга"], "discovery_keywords": ["новости", "ДТП"]}
    loc, kw = _read_region_discovery_config(_region(cfg))
    assert loc == ["Тужа", "Шешурга"]
    assert kw == ["новости", "ДТП"]


def test_config_string_split_by_newline():
    cfg = {"localities": "Тужа\nШешурга\nМихайловское"}
    loc, kw = _read_region_discovery_config(_region(cfg))
    assert loc == ["Тужа", "Шешурга", "Михайловское"]
    assert kw == []


def test_config_string_split_by_comma_and_semicolon():
    cfg = {"discovery_keywords": "новости, ДТП; объявления"}
    _, kw = _read_region_discovery_config(_region(cfg))
    assert kw == ["новости", "ДТП", "объявления"]


def test_config_dedup_preserves_order_case_insensitive():
    cfg = {"localities": ["Тужа", "тужа", "ТУЖА", "Шешурга"]}
    loc, _ = _read_region_discovery_config(_region(cfg))
    assert loc == ["Тужа", "Шешурга"]


def test_config_strips_whitespace_and_empty_items():
    cfg = {"localities": ["  Тужа  ", "", "  ", "Шешурга"]}
    loc, _ = _read_region_discovery_config(_region(cfg))
    assert loc == ["Тужа", "Шешурга"]


def test_config_ignores_non_list_non_string():
    cfg = {"localities": {"unexpected": "type"}}
    loc, _ = _read_region_discovery_config(_region(cfg))
    assert loc == []
