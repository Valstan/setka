"""Диспетчер раскрутки: слоты, тихие часы и реакция на отказ ВК.

Проверяем то, что дорого стоит на живой стене: границу недельного слота (от неё
зависит квота «одно промо в неделю на донора»), окно тишины через полночь и то,
что ответ ВК «ты спамишь» действительно останавливает модуль, а не одно действие.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from modules.promotion import dispatcher as disp
from modules.promotion.vk_errors import classify_promo_error


class TestSlotKey:
    def test_same_week_gives_same_slot(self):
        monday = datetime(2026, 8, 24, 10, 0)
        sunday = datetime(2026, 8, 30, 23, 0)
        assert disp.slot_key_week(monday) == disp.slot_key_week(sunday)

    def test_next_week_differs(self):
        assert disp.slot_key_week(datetime(2026, 8, 30)) != disp.slot_key_week(
            datetime(2026, 8, 31)
        )

    def test_new_year_boundary_stays_one_slot(self):
        # 31 декабря и 1 января 2026-го — одна ISO-неделя. «Номер недели года»
        # разорвал бы её, и донор получил бы два промо подряд на стыке лет.
        assert disp.slot_key_week(datetime(2026, 12, 31)) == disp.slot_key_week(
            datetime(2027, 1, 1)
        )

    def test_format_is_iso_week(self):
        assert disp.slot_key_week(datetime(2026, 8, 28)).startswith("2026-W")


class TestQuietHours:
    def test_window_wraps_over_midnight(self):
        # Окно 19→10 проходит через полночь: и 23:00, и 03:00 внутри него.
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 23), 19, 10) is True
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 3), 19, 10) is True

    def test_working_hours_are_not_quiet(self):
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 12), 19, 10) is False
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 10), 19, 10) is False
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 18), 19, 10) is False

    def test_boundary_hours(self):
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 19), 19, 10) is True
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 9), 19, 10) is True

    def test_equal_bounds_mean_no_quiet_time(self):
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 3), 10, 10) is False

    def test_plain_window_without_wrap(self):
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 2), 1, 5) is True
        assert disp.in_quiet_hours(datetime(2026, 8, 28, 7), 1, 5) is False


class TestErrorActionSideEffects:
    @pytest.mark.asyncio
    async def test_flood_control_pauses_module_not_donor(self, monkeypatch):
        # Код 9 — про нас, а не про конкретную стену: банить донора и продолжать
        # значило бы долбиться дальше ровно тогда, когда ВК сказал «хватит».
        session = AsyncMock()
        action = classify_promo_error(9)
        await disp.apply_error_action(session, action=action, donor_group_id=-123)

        assert session.execute.await_count == 1
        statement = str(session.execute.await_args.args[0])
        assert "promo_settings" in statement.lower()

    @pytest.mark.asyncio
    async def test_ad_code_blacklists_only_the_donor(self):
        # Код 219 — про конкретную стену: модуль работает дальше.
        session = AsyncMock()
        action = classify_promo_error(219)
        await disp.apply_error_action(session, action=action, donor_group_id=-179306667)

        statement = str(session.execute.await_args.args[0])
        assert "promo_donor_blacklist" in statement.lower()

    @pytest.mark.asyncio
    async def test_retryable_error_changes_nothing(self):
        session = AsyncMock()
        action = classify_promo_error(1234)
        await disp.apply_error_action(session, action=action, donor_group_id=-1)

        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blacklist_without_donor_is_noop(self):
        # Дайджест областной ленты может прийти без донора — не падаем.
        session = AsyncMock()
        await disp.apply_error_action(
            session, action=classify_promo_error(214), donor_group_id=None
        )
        session.execute.assert_not_awaited()


class TestStaleWindow:
    def test_stale_threshold_is_days_not_hours(self):
        # Раскрутка работает раз в неделю на донора. Порог в часах, как у сводок,
        # заставил бы сторожа орать на штатную тишину.
        assert disp.STALE_DAYS >= 2

    def test_pending_reclaim_window_is_longer_than_any_run(self):
        # Прогон — не больше нескольких действий по 5 с. Десять минут с запасом.
        assert disp.STALE_PENDING_SECONDS >= 5 * 60


class TestHeartbeatIsBestEffort:
    def test_missing_redis_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(disp, "_redis", lambda: None)
        disp.touch_heartbeat()  # не должно бросить

    def test_broken_redis_does_not_raise(self, monkeypatch):
        class Boom:
            def setex(self, *a, **kw):
                raise RuntimeError("redis down")

        monkeypatch.setattr(disp, "_redis", lambda: Boom())
        disp.touch_heartbeat()  # наблюдаемость не валит работу
