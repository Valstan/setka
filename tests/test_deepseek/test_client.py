"""Общий клиент DeepSeek — единственная точка входа к движку (D-024).

Тесты держат контракт, на который опираются три потребителя: конвейер,
категоризатор discovery и черновики ответов. Форма отказа тут важнее удачного
пути — все три вызывающих ветвятся именно по ``reason``.
"""

from __future__ import annotations

import pytest

from modules import deepseek_client as dc


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)


def _api(status, payload):
    def fake(body, *, api_key, base_url, timeout):
        fake.body = body
        fake.api_key = api_key
        return status, payload
    return fake


def _completion(content, usage=None):
    return {"choices": [{"message": {"content": content}}], "usage": usage or {"total_tokens": 42}}


def test_happy_path_returns_content_model_and_usage(monkeypatch):
    monkeypatch.setattr(dc, "call_api", _api(200, _completion("привет")))
    out = dc.chat(user="вопрос")
    assert out == {"ok": True, "content": "привет", "model": "deepseek-chat", "usage": {"total_tokens": 42}}


def test_missing_key_is_refusal_not_crash(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert dc.chat(user="вопрос") == {"ok": False, "reason": "no_api_key"}


def test_empty_prompt_short_circuits_before_network(monkeypatch):
    called = {"n": 0}

    def fake(*a, **kw):
        called["n"] += 1
        return 200, _completion("x")

    monkeypatch.setattr(dc, "call_api", fake)
    assert dc.chat(user="   ")["reason"] == "empty_prompt"
    assert called["n"] == 0


def test_network_failure_is_reported(monkeypatch):
    monkeypatch.setattr(dc, "call_api", _api(0, {"error": "timeout"}))
    out = dc.chat(user="вопрос")
    assert out["ok"] is False
    assert out["reason"] == "network"
    assert out["detail"] == "timeout"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_status_survives_in_reason(monkeypatch, status):
    """401 (чинит человек) и 429 (пройдёт само) — разные решения у вызывающего,
    поэтому код не схлопывается в общий 'http_error'."""
    monkeypatch.setattr(dc, "call_api", _api(status, {"error": "nope"}))
    assert dc.chat(user="вопрос")["reason"] == f"http_{status}"


def test_blank_content_is_empty_response(monkeypatch):
    monkeypatch.setattr(dc, "call_api", _api(200, _completion("   ")))
    assert dc.chat(user="вопрос")["reason"] == "empty_response"


def test_missing_choices_is_empty_response(monkeypatch):
    monkeypatch.setattr(dc, "call_api", _api(200, {}))
    assert dc.chat(user="вопрос")["reason"] == "empty_response"


def test_system_prompt_is_sent_first(monkeypatch):
    fake = _api(200, _completion("ok"))
    monkeypatch.setattr(dc, "call_api", fake)
    dc.chat(user="u", system="s")
    assert [m["role"] for m in fake.body["messages"]] == ["system", "user"]


def test_blank_system_prompt_is_omitted(monkeypatch):
    fake = _api(200, _completion("ok"))
    monkeypatch.setattr(dc, "call_api", fake)
    dc.chat(user="u", system="   ")
    assert [m["role"] for m in fake.body["messages"]] == ["user"]


def test_json_mode_is_opt_in(monkeypatch):
    fake = _api(200, _completion("{}"))
    monkeypatch.setattr(dc, "call_api", fake)
    dc.chat(user="u")
    assert "response_format" not in fake.body
    dc.chat(user="u", json_object=True)
    assert fake.body["response_format"] == {"type": "json_object"}


def test_model_override_wins_over_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "from-env")
    fake = _api(200, _completion("ok"))
    monkeypatch.setattr(dc, "call_api", fake)
    out = dc.chat(user="u", model="explicit")
    assert fake.body["model"] == "explicit"
    assert out["model"] == "explicit"


def test_explicit_api_key_wins_over_env(monkeypatch):
    fake = _api(200, _completion("ok"))
    monkeypatch.setattr(dc, "call_api", fake)
    dc.chat(user="u", api_key="explicit-key")
    assert fake.api_key == "explicit-key"
