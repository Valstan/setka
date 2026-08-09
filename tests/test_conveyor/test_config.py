"""Тесты конфига конвейера — прежде всего умолчаний, которые решают, полетит ли пост наружу."""

from __future__ import annotations

import pytest

from config import content_conveyor as cc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "CONVEYOR_DISABLED",
        "CONVEYOR_SITES",
        "CONVEYOR_SOURCE_DAYS",
        "CONVEYOR_BATCH_MAX",
    ):
        monkeypatch.delenv(name, raising=False)


class TestActivation:
    def test_no_env_means_no_active_sites(self):
        """Умолчание не публикует: забытая переменная на новом боксе — тишина, не рассылка."""
        assert cc.get_active_sites() == []

    def test_site_activates_by_key(self, monkeypatch):
        monkeypatch.setenv("CONVEYOR_SITES", "vmalmyzhe")
        assert [s["key"] for s in cc.get_active_sites()] == ["vmalmyzhe"]

    def test_kill_switch_beats_allowlist(self, monkeypatch):
        monkeypatch.setenv("CONVEYOR_SITES", "vmalmyzhe")
        monkeypatch.setenv("CONVEYOR_DISABLED", "1")
        assert cc.get_active_sites() == []

    def test_unknown_key_is_ignored_not_fatal(self, monkeypatch):
        monkeypatch.setenv("CONVEYOR_SITES", "opechatka,vmalmyzhe")
        assert [s["key"] for s in cc.get_active_sites()] == ["vmalmyzhe"]

    def test_separators_and_case(self, monkeypatch):
        monkeypatch.setenv("CONVEYOR_SITES", " VMALMYZHE ; ")
        assert [s["key"] for s in cc.get_active_sites()] == ["vmalmyzhe"]


class TestSiteDescriptions:
    def test_keys_are_unique(self):
        keys = [s["key"] for s in cc.SITES]
        assert len(keys) == len(set(keys))

    def test_every_site_has_required_fields(self):
        for s in cc.SITES:
            for field in ("key", "title", "ingest_url", "key_env", "source_region"):
                assert str(s.get(field) or "").strip(), f"{s.get('key')}: пустое {field}"

    def test_ingest_urls_are_https(self):
        for s in cc.SITES:
            assert str(s["ingest_url"]).startswith("https://"), s["key"]

    def test_key_env_never_collides_with_gateway_prefix(self):
        """GATEWAY_KEY_* — ключи ВХОДЯЩИХ в наш VK-шлюз (config/gateway.py).

        Исходящий ключ доставки под тем же префиксом при недоступной БД начинает
        работать как пропуск в наш шлюз (resolve_gateway_key_name падает на env).
        Тест держит границу направлений именами, а не памятью автора.
        """
        for s in cc.SITES:
            assert str(s["key_env"]).startswith("INGEST_KEY_"), s["key"]
            assert not str(s["key_env"]).startswith("GATEWAY_KEY_"), s["key"]

    def test_get_site_by_key_is_case_insensitive(self):
        assert cc.get_site("VMALMYZHE")["title"] == "вМалмыже.РФ"
        assert cc.get_site("нет-такого") is None

    def test_get_site_returns_copy(self):
        first = cc.get_site("vmalmyzhe")
        first["title"] = "ИСПОРЧЕНО"
        assert cc.get_site("vmalmyzhe")["title"] != "ИСПОРЧЕНО"


class TestKeysAndLimits:
    def test_ingest_key_read_from_named_env(self, monkeypatch):
        monkeypatch.setenv("INGEST_KEY_VMALMYZHE", "s3cret")
        assert cc.get_ingest_key(cc.get_site("vmalmyzhe")) == "s3cret"

    def test_missing_key_is_empty_not_exception(self, monkeypatch):
        monkeypatch.delenv("INGEST_KEY_VMALMYZHE", raising=False)
        assert cc.get_ingest_key(cc.get_site("vmalmyzhe")) == ""

    @pytest.mark.parametrize(
        "raw,expected", [("7", 7), ("0", 1), ("999", 30), ("мусор", 3), (None, 3)]
    )
    def test_source_days_bounds(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("CONVEYOR_SOURCE_DAYS", raising=False)
        else:
            monkeypatch.setenv("CONVEYOR_SOURCE_DAYS", raw)
        assert cc.get_source_days() == expected

    @pytest.mark.parametrize("raw,expected", [("5", 5), ("0", 1), ("500", 100), ("мусор", 20)])
    def test_batch_max_bounds(self, monkeypatch, raw, expected):
        monkeypatch.setenv("CONVEYOR_BATCH_MAX", raw)
        assert cc.get_batch_max() == expected
