"""Сторож «ИИ-фильтр молчит» (инцидент 2026-08-19).

Инцидент: ключ DeepSeek не доехал до celery-воркера, и трое суток
``classify_pending_posts`` возвращала ``status: ok``, забирая по 200 постов и
не размечая ни одного. Все существовавшие сигналы показывали здоровье —
сервисы ``active``, health 200, beat шлёт, worker принимает. Тесты ниже держат
единственный сигнал, который в том инциденте был правдивым: свежесть следа
работы (последнего вердикта в БД).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from modules.classifier import heartbeat as hb

NOW = datetime(2026, 8, 19, 12, 0, 0)


def test_fresh_verdict_is_not_alerted():
    status = hb.maybe_alert_stale_classifier(
        last_verdict_at=NOW - timedelta(hours=2),
        telegram_token="t",
        chat_id="1",
        now=NOW,
    )
    assert status == "fresh"


def test_no_verdicts_at_all_is_not_alerted():
    """«Свежая база» неотличима от «сломано навсегда» — молчим, как и сторож сводок."""
    assert hb.maybe_alert_stale_classifier(last_verdict_at=None, now=NOW) == "unknown:no-verdicts"


def test_stale_verdict_without_telegram_config_is_reported_not_swallowed():
    status = hb.maybe_alert_stale_classifier(
        last_verdict_at=NOW - timedelta(hours=72),
        telegram_token=None,
        chat_id=None,
        now=NOW,
    )
    assert status == "skipped:no-telegram-config"


def test_incident_shape_triggers_alert(monkeypatch):
    """Ровно та форма инцидента: последний вердикт 16.08 03:35, «сейчас» 19.08."""
    sent = {}

    class _Resp:
        status_code = 200
        text = "ok"

    class _Requests:
        @staticmethod
        def post(url, json=None, timeout=None):
            sent["url"] = url
            sent["text"] = json["text"]
            return _Resp()

    monkeypatch.setitem(__import__("sys").modules, "requests", _Requests)
    monkeypatch.setattr(hb, "_redis", lambda: None)

    status = hb.maybe_alert_stale_classifier(
        last_verdict_at=datetime(2026, 8, 16, 3, 35, 46),
        backlog=6360,
        telegram_token="tok",
        chat_id="42",
        now=NOW,
    )
    assert status == "alert-sent"
    # Цена простоя в тексте: без неё алёрт не отличить от «мелочь, потом».
    assert "6360" in sent["text"]
    assert "ИИ-фильтр молчит" in sent["text"]


def test_cooldown_suppresses_repeat(monkeypatch):
    class _Client:
        def get(self, key):
            return "1"

        def setex(self, *a):  # pragma: no cover — не должен вызываться
            raise AssertionError("cooldown не должен обновляться при подавлении")

    monkeypatch.setattr(hb, "_redis", lambda: _Client())
    status = hb.maybe_alert_stale_classifier(
        last_verdict_at=NOW - timedelta(hours=48),
        telegram_token="t",
        chat_id="1",
        now=NOW,
    )
    assert status == "skipped:cooldown"


@pytest.mark.parametrize(
    "value",
    ["2026-08-16T03:35:46", "2026-08-16T03:35:46Z", datetime(2026, 8, 16, 3, 35, 46)],
)
def test_age_accepts_iso_and_datetime(value):
    """``health_stats`` отдаёт метку строкой — разбор внутри, а не у вызывающего."""
    age = hb.verdict_age_hours(value, now=NOW)
    assert age is not None
    assert 80 < age < 81


def test_age_is_naive_utc_not_local():
    """Колонка — наивный UTC. Сравнение с локальным «сейчас» промахнулось бы на
    три часа (MSK) и на границе порога дало бы неверный вердикт."""
    assert hb.verdict_age_hours(NOW - timedelta(hours=9), now=NOW) == pytest.approx(9.0)


def test_clock_skew_does_not_produce_negative_age():
    assert hb.verdict_age_hours(NOW + timedelta(hours=1), now=NOW) == 0.0
