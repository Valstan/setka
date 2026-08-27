"""Сторож раскрутки — ветки статус-строки.

Сторож обязан молчать в каждом случае, где тишина штатна. Ложный алёрт дороже
пропущенного: он приучает не смотреть на уведомления, и настоящую поломку тогда
тоже не заметят.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.promotion import dispatcher as disp


def _settings(**overrides):
    row = MagicMock()
    row.paused_until = overrides.get("paused_until")
    row.channels = overrides.get("channels", {"promo_post": {"enabled": True, "dry_run": False}})
    return row


def _session(settings, *, last_published=None, active_targets=1):
    """Сессия, отвечающая по порядку: настройки → счётчик целей → последняя дата."""
    session = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalar_one_or_none.return_value = settings

    count_result = MagicMock()
    count_result.scalar.return_value = active_targets

    last_result = MagicMock()
    last_result.scalar.return_value = last_published

    session.execute = AsyncMock(side_effect=[settings_result, count_result, last_result])
    return session


@pytest.fixture(autouse=True)
def _module_enabled(monkeypatch):
    """По умолчанию считаем модуль включённым — иначе всё упирается в первый гейт."""
    monkeypatch.setattr(disp, "promo_disabled", lambda: False)


class TestSilentCases:
    @pytest.mark.asyncio
    async def test_no_settings_row(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        assert await disp.maybe_alert_stale_promo(session) == "skipped:no-settings"

    @pytest.mark.asyncio
    async def test_module_disabled(self, monkeypatch):
        monkeypatch.setattr(disp, "promo_disabled", lambda: True)
        session = _session(_settings())
        assert await disp.maybe_alert_stale_promo(session) == "skipped:module-disabled"

    @pytest.mark.asyncio
    async def test_module_paused_after_vk_said_stop(self):
        settings = _settings(paused_until=datetime.utcnow() + timedelta(hours=5))
        assert await disp.maybe_alert_stale_promo(_session(settings)) == "skipped:paused"

    @pytest.mark.asyncio
    async def test_dry_run_is_a_mode_not_a_failure(self):
        # Этап 1 — все каналы сухие. Это рабочий режим, а не поломка.
        settings = _settings(channels={"promo_post": {"enabled": True, "dry_run": True}})
        assert await disp.maybe_alert_stale_promo(_session(settings)) == "skipped:dry-run"

    @pytest.mark.asyncio
    async def test_nothing_to_promote(self):
        session = _session(_settings(), active_targets=0)
        assert await disp.maybe_alert_stale_promo(session) == "no-targets"

    @pytest.mark.asyncio
    async def test_never_published_is_indistinguishable_from_fresh_start(self):
        # Пустой сигнал не алёртит: «только включили» и «сломано навсегда»
        # отсюда неразличимы.
        session = _session(_settings(), last_published=None)
        assert await disp.maybe_alert_stale_promo(session) == "unknown:never-published"

    @pytest.mark.asyncio
    async def test_recent_publication_is_fresh(self):
        session = _session(_settings(), last_published=datetime.utcnow() - timedelta(hours=2))
        assert await disp.maybe_alert_stale_promo(session) == "fresh"


class TestAlerting:
    @pytest.mark.asyncio
    async def test_stale_without_telegram_config_is_skipped(self):
        old = datetime.utcnow() - timedelta(days=disp.STALE_DAYS + 1)
        session = _session(_settings(), last_published=old)
        assert await disp.maybe_alert_stale_promo(session) == "skipped:no-telegram-config"

    @pytest.mark.asyncio
    async def test_stale_sends_alert_and_sets_cooldown(self, monkeypatch):
        old = datetime.utcnow() - timedelta(days=disp.STALE_DAYS + 1)
        session = _session(_settings(), last_published=old)

        redis = MagicMock()
        redis.get.return_value = None
        monkeypatch.setattr(disp, "_redis", lambda: redis)

        response = MagicMock()
        response.status_code = 200
        import requests

        monkeypatch.setattr(requests, "post", lambda *a, **kw: response)

        status = await disp.maybe_alert_stale_promo(session, telegram_token="t", chat_id="-100")
        assert status == "alert-sent"
        redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cooldown_suppresses_repeat(self, monkeypatch):
        old = datetime.utcnow() - timedelta(days=disp.STALE_DAYS + 1)
        session = _session(_settings(), last_published=old)

        redis = MagicMock()
        redis.get.return_value = "1"
        monkeypatch.setattr(disp, "_redis", lambda: redis)

        status = await disp.maybe_alert_stale_promo(session, telegram_token="t", chat_id="-100")
        assert status == "skipped:cooldown"
