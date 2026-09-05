"""Швы ЛС-транспорта: лимитер, учёт и пауза канала в тестах не ходят в Redis.

С Этапа 3 ``vk_actions._call_with_fallback`` тормозится общим лимитером и
считает расход, а ``send_message`` проверяет паузу DM-канала — всё это Redis.
Тест, которому нужен свой лимитер или пауза, патчит поверх.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _dm_transport_offline(monkeypatch):
    from modules.ad_cabinet import dm_channel
    from modules.notifications import vk_actions

    monkeypatch.setattr(vk_actions, "_throttle", lambda token, op: None)
    monkeypatch.setattr(dm_channel, "_redis", lambda: None)
    dm_channel.reset_for_tests()
    yield
    dm_channel.reset_for_tests()
