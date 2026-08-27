"""Тесты API раздела «Раскрутка» (web/api/promotion).

Чистые хелперы — без БД; эндпоинты — с AsyncMock-сессией, как в
tests/test_api/test_subscriber_growth.py. Ни один тест не ходит в VK: на этапе 0
модуль вообще не имеет права туда ходить.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from web.api import promotion as api


def _region(**overrides):
    region = MagicMock()
    region.id = overrides.get("id", 1)
    region.code = overrides.get("code", "suna")
    region.name = overrides.get("name", "СУНА - ИНФО")
    region.kind = overrides.get("kind", "raion")
    region.is_active = overrides.get("is_active", True)
    region.vk_group_id = overrides.get("vk_group_id", -241117113)
    region.local_hashtags = overrides.get("local_hashtags", "")
    region.vk_city_id = overrides.get("vk_city_id", None)
    region.center_city = overrides.get("center_city", "")
    region.config = overrides.get("config", None)
    return region


class TestScreenName:
    def test_uses_cached_screen_name(self):
        region = _region(config={"screen_name": "suna_info43"})
        assert api._screen_name(region) == "suna_info43"

    def test_none_without_cache(self):
        assert api._screen_name(_region()) is None
        assert api._screen_name(_region(config={})) is None
        assert api._screen_name(_region(config={"screen_name": ""})) is None

    def test_survives_non_dict_config(self):
        assert api._screen_name(_region(config="broken")) is None


class TestHygiene:
    def test_fresh_district_has_every_gap(self):
        # Ровно состояние Суны на 28.08: ни хэштегов, ни города, ни ключа.
        result = api._hygiene(_region(), vk_type="group", has_token=False)
        assert "хэштеги" in result["gaps"]
        assert "город" in result["gaps"]
        assert "ключ сообщества" in result["gaps"]
        assert "группа вместо публичной страницы" in result["gaps"]
        assert result["has_hashtags"] is False
        assert result["is_public_page"] is False

    def test_configured_district_has_no_gaps(self):
        region = _region(
            code="mi",
            local_hashtags="#малмыж,#малмыжский_район",
            vk_city_id=42,
            center_city="Малмыж",
        )
        result = api._hygiene(region, vk_type="page", has_token=True)
        assert result["gaps"] == []
        assert result["is_public_page"] is True

    def test_known_adjective_is_reported(self):
        # UI отделяет «хэштега нет» от «хэштег не из чего собрать».
        assert api._hygiene(_region(), vk_type="", has_token=True)["hashtags_known"] is True
        unknown = _region(code="unknown_district")
        assert api._hygiene(unknown, vk_type="", has_token=True)["hashtags_known"] is False

    def test_blank_hashtags_count_as_missing(self):
        result = api._hygiene(_region(local_hashtags="   "), vk_type="", has_token=True)
        assert "хэштеги" in result["gaps"]

    def test_unknown_vk_type_is_not_reported_as_group(self):
        result = api._hygiene(_region(), vk_type="", has_token=True)
        assert "группа вместо публичной страницы" not in result["gaps"]


@pytest.fixture
def settings_row(monkeypatch):
    """Подменяет чтение строки настроек — правка живёт только внутри теста."""
    settings = MagicMock()
    settings.threshold_members = 300
    settings.graduate_members = 400
    settings.to_dict.return_value = {}
    monkeypatch.setattr(api, "_settings_row", AsyncMock(return_value=settings))
    return settings


class TestSettingsPut:
    @pytest.mark.asyncio
    async def test_none_fields_are_not_touched(self, settings_row):
        await api.put_settings(api.SettingsPut(threshold_members=250), AsyncMock())

        assert settings_row.threshold_members == 250
        assert settings_row.graduate_members == 400  # не передавали — не трогаем

    @pytest.mark.asyncio
    async def test_graduate_below_threshold_is_lifted(self, settings_row):
        # Порог выхода ниже порога входа превращает гистерезис в мигание:
        # район зачислялся бы и выпускался на каждом ночном прогоне.
        await api.put_settings(
            api.SettingsPut(threshold_members=300, graduate_members=100), AsyncMock()
        )

        assert settings_row.graduate_members == 300

    @pytest.mark.asyncio
    async def test_channels_dict_is_applied(self, settings_row):
        payload = api.SettingsPut(channels={"promo_post": {"enabled": False}})
        await api.put_settings(payload, AsyncMock())

        assert settings_row.channels == {"promo_post": {"enabled": False}}
