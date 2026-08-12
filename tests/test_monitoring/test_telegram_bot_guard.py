"""Сторож целостности Telegram-ботов (инцидент 2026-08-12).

Приёмка воспроизводит фактический угон: имя заменено на рекламу, описание — на
реферальную ссылку, поставлен чужой вебхук. Каждое из трёх обязано ловиться
поодиночке — атакующий может сделать только одно.
"""

from __future__ import annotations

import pytest

from modules import telegram_bot_guard as guard
from modules.telegram_bot_guard import BotExpectation, run_guard

EXPECTED = {
    "AFONYA": BotExpectation(
        username="malm_info_bot",
        name="Малмыж-Инфо",
        description="Малмыж-Инфо",
        short_description="Малмыж-Инфо",
    )
}

HEALTHY = {
    "getMe": {"ok": True, "result": {"username": "malm_info_bot", "first_name": "Малмыж-Инфо"}},
    "getMyDescription": {"ok": True, "result": {"description": "Малмыж-Инфо"}},
    "getMyShortDescription": {"ok": True, "result": {"short_description": "Малмыж-Инфо"}},
    "getWebhookInfo": {"ok": True, "result": {"url": ""}},
}


@pytest.fixture
def api(monkeypatch):
    """Подменяемый Bot API: dict метод → ответ."""
    state = {"responses": dict(HEALTHY), "calls": []}

    def fake_call(token, method):
        state["calls"].append(method)
        resp = state["responses"].get(method)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(guard, "_call", fake_call)
    return state


def _fields(result):
    return {f.field_name for f in result.findings}


def test_healthy_bot_has_no_findings(api):
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert result.ok
    assert result.checked == ["AFONYA"]


def test_renamed_bot_is_caught(api):
    api["responses"]["getMe"] = {
        "ok": True,
        "result": {"username": "malm_info_bot", "first_name": "BEST CASINO MINI-APP @Xstakerobot"},
    }
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert "name" in _fields(result)
    assert "CASINO" in result.findings[0].actual


def test_hijacked_description_is_caught(api):
    api["responses"]["getMyDescription"] = {
        "ok": True,
        "result": {"description": "🔥 Best AI Porn here: https://t.me/sexoz_bot?start=ref_c39"},
    }
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert "description" in _fields(result)


def test_foreign_webhook_is_caught(api):
    api["responses"]["getWebhookInfo"] = {
        "ok": True,
        "result": {"url": "https://ssh.inkognit.org:8443/hook/5945194659/6f4b06"},
    }
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert "webhook" in _fields(result)


def test_revoked_token_is_a_finding_not_a_skip(api):
    """401 от API — не сетевой шум: токен отозван, а мы об этом не знаем."""
    api["responses"]["getMe"] = {"ok": False, "description": "Unauthorized"}
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert "getMe" in _fields(result)
    assert not result.checked


def test_network_error_is_skipped_not_alerted(api):
    """Обрыв связи не инцидент безопасности — иначе сторож станет шумом и его
    выключат ровно к тому дню, когда он понадобится."""
    api["responses"]["getMe"] = ConnectionError("boom")
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    assert result.ok
    assert result.skipped == {"AFONYA": "network"}


def test_unknown_bot_without_expectation_is_a_finding(api):
    result = run_guard({"AFONYA": "tok", "STRANGER": "tok2"}, EXPECTED)
    assert any(f.bot == "STRANGER" for f in result.findings)


def test_alias_token_is_not_reported_as_unknown(api):
    """Back-compat ALERT — то же значение под вторым именем, а не новый бот."""
    result = run_guard({"AFONYA": "tok", "ALERT": "tok"}, EXPECTED)
    assert result.ok
    assert result.skipped.get("ALERT") == "alias"


def test_missing_token_for_expected_bot_is_skipped(api):
    result = run_guard({}, EXPECTED)
    assert result.ok
    assert result.skipped == {"AFONYA": "no-token"}


def test_alert_text_names_fields_without_leaking_tokens(api):
    api["responses"]["getWebhookInfo"] = {"ok": True, "result": {"url": "https://evil.example/x"}}
    result = run_guard({"AFONYA": "tok"}, EXPECTED)
    text = guard.format_alert(result)
    assert "AFONYA.webhook" in text
    assert "evil.example" in text
    assert "tok" not in text.replace("токен", "").replace("Токен", "")


def test_maybe_alert_respects_cooldown(api, monkeypatch):
    api["responses"]["getMe"] = {
        "ok": True,
        "result": {"username": "malm_info_bot", "first_name": "угнано"},
    }
    monkeypatch.setattr(guard, "EXPECTED_BOTS", EXPECTED)

    class FakeRedis:
        def __init__(self):
            self.value = "1"

        def get(self, key):
            return self.value

        def setex(self, *a):  # pragma: no cover - не должно вызваться
            raise AssertionError("cooldown active, must not send")

    status = guard.maybe_alert(
        tokens={"AFONYA": "tok"},
        telegram_token="alert-tok",
        chat_id="42",
        redis_client=FakeRedis(),
    )
    assert status == "skipped:cooldown"


def test_maybe_alert_ok_when_everything_matches(api, monkeypatch):
    monkeypatch.setattr(guard, "EXPECTED_BOTS", EXPECTED)
    status = guard.maybe_alert(tokens={"AFONYA": "tok"}, telegram_token="a", chat_id="42")
    assert status == "ok"
