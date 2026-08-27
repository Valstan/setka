"""Три уровня, гасящие публикацию, и порядок их приоритета.

Смысл теста — не «функция возвращает dataclass», а инвариант: **ни один
переключатель владельца не может пересилить env-килл-свитч или паузу, выданную
самим ВК**. Если этот порядок сломается, галочка в интерфейсе начнёт публиковать
в момент, когда публиковать запрещено.
"""

from datetime import datetime, timedelta

from modules.promotion.settings import (
    DEFAULT_CHANNELS,
    DISPATCH_CHANNELS,
    merge_channels,
    module_paused,
    resolve_channel,
)

LIVE = {"promo_post": {"enabled": True, "dry_run": False}}


class TestDefaults:
    def test_every_channel_starts_dry(self):
        # Канал, включённый по недосмотру, обязан сначала показать текст,
        # а не отправить его.
        for name, row in DEFAULT_CHANNELS.items():
            assert row["dry_run"] is True, name

    def test_dispatch_channels_are_known(self):
        for name in DISPATCH_CHANNELS:
            assert name in DEFAULT_CHANNELS


class TestMergeChannels:
    def test_missing_settings_fall_back_to_defaults(self):
        merged = merge_channels(None)
        assert merged["promo_post"] == {"enabled": True, "dry_run": True}

    def test_stored_values_win(self):
        merged = merge_channels({"promo_post": {"dry_run": False}})
        assert merged["promo_post"]["dry_run"] is False
        assert merged["promo_post"]["enabled"] is True  # не передали — дефолт

    def test_unknown_channel_is_dropped(self):
        # Опечатка в JSON не должна создавать «канал-призрак», видимый в UI и
        # ничего не делающий.
        merged = merge_channels({"promo_pots": {"enabled": True}})
        assert "promo_pots" not in merged

    def test_garbage_row_does_not_crash(self):
        merged = merge_channels({"promo_post": "включён"})
        assert merged["promo_post"] == {"enabled": True, "dry_run": True}


class TestModulePaused:
    def test_none_is_not_paused(self):
        assert module_paused(None) is False

    def test_future_is_paused(self):
        assert module_paused(datetime.utcnow() + timedelta(hours=1)) is True

    def test_past_is_not_paused(self):
        assert module_paused(datetime.utcnow() - timedelta(hours=1)) is False


class TestResolveChannel:
    def test_live_channel_publishes(self):
        state = resolve_channel("promo_post", LIVE, module_disabled=False)
        assert state.publishes is True

    def test_env_kill_switch_beats_everything(self):
        state = resolve_channel("promo_post", LIVE, module_disabled=True)
        assert state.publishes is False
        assert state.dry_run is True
        assert "PROMO_DISABLED" in state.reason

    def test_vk_pause_beats_owner_switch(self):
        # Пауза выдана самим ВК (код 9 или 14) — галочка её не отменяет.
        state = resolve_channel("promo_post", LIVE, module_disabled=False, paused=True)
        assert state.publishes is False
        assert "паузе" in state.reason

    def test_disabled_channel_does_not_publish(self):
        state = resolve_channel(
            "promo_post",
            {"promo_post": {"enabled": False, "dry_run": False}},
            module_disabled=False,
        )
        assert state.publishes is False
        assert state.reason == "канал выключен"

    def test_dry_run_channel_does_not_publish(self):
        state = resolve_channel("promo_post", None, module_disabled=False)
        assert state.enabled is True
        assert state.publishes is False
        assert "сухой прогон" in state.reason

    def test_unknown_channel_is_closed(self):
        state = resolve_channel("нет_такого", {}, module_disabled=False)
        assert state.publishes is False
