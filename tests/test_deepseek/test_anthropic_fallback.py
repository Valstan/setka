"""Запасной движок Anthropic и развилка провайдера (подпорка на отказ DeepSeek 2026-09-22).

Проверяем ровно то, что ломается молча: развилку (дефолт обязан остаться
DeepSeek), контракт отказов (все пять потребителей ветвятся по ``reason``),
разметку префикс-кэша и ловушку ``temperature`` — на Sonnet 5 sampling удалён и
возвращает 400 на КАЖДОМ вызове, то есть ошибка здесь кладёт весь прогон.

SDK подменяется фейковым модулем в ``sys.modules``: импорт в
``anthropic_client.chat`` ленивый, так что подмена работает без пакета.
"""

from __future__ import annotations

import sys
import types

import pytest

from modules import anthropic_client as ac
from modules import deepseek_client as dc


class _Usage:
    def __init__(self, fresh=100, read=900, write=0, out=50):
        self.input_tokens = fresh
        self.cache_read_input_tokens = read
        self.cache_creation_input_tokens = write
        self.output_tokens = out


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, blocks, stop_reason="end_turn", usage=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = usage if usage is not None else _Usage()


class _APIStatusError(Exception):
    def __init__(self, status_code, message=""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _APIConnectionError(Exception):
    pass


def _fake_sdk(monkeypatch, response=None, raises=None):
    """Подсунуть фейковый пакет ``anthropic``; вернуть список перехваченных kwargs."""
    seen: list = []

    class _Messages:
        def create(self, **kwargs):
            seen.append(kwargs)
            if raises is not None:
                raise raises
            return response

    class _Client:
        def __init__(self, **kwargs):
            seen.append({"__client__": kwargs})
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Client
    mod.APIStatusError = _APIStatusError
    mod.APIConnectionError = _APIConnectionError
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


# ───────── развилка провайдера ─────────


def test_default_provider_stays_deepseek(monkeypatch):
    """Без явного переключения ничего не меняется — D-024 остаётся в силе."""
    called = {}

    def _fake_call(body, *, api_key, base_url, timeout, label="deepseek"):
        called["hit"] = True
        return 200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(dc, "call_api", _fake_call)
    out = dc.chat(user="привет")
    assert out["ok"] is True
    assert called.get("hit") is True, "дефолт увёл вызов не в DeepSeek"


def test_provider_switch_routes_to_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    seen = _fake_sdk(monkeypatch, response=_Response([_Block("text", '{"ok": 1}')]))

    def _boom(*a, **kw):  # DeepSeek не должен быть тронут вовсе
        raise AssertionError("вызов ушёл в DeepSeek при LLM_PROVIDER=anthropic")

    monkeypatch.setattr(dc, "call_api", _boom)
    out = dc.chat(user="привет", system="постулаты")
    assert out["ok"] is True
    assert out["content"] == '{"ok": 1}'
    assert out["model"] == "claude-haiku-4-5", "дефолтная модель не Haiku 4.5"
    assert seen, "SDK не был вызван"


def test_unknown_provider_value_falls_back_to_deepseek(monkeypatch):
    """Опечатка в env не должна молча уводить прогон на другой движок."""
    monkeypatch.setenv("LLM_PROVIDER", "antropic")  # опечатка
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(
        dc,
        "call_api",
        lambda body, **kw: (200, {"choices": [{"message": {"content": "x"}}]}),
    )
    assert dc.chat(user="привет")["ok"] is True


# ───────── совместимость с настоящим SDK ─────────


def test_call_kwargs_exist_in_real_sdk_signature(monkeypatch):
    """Каждый ключ, который мы шлём, обязан существовать в НАСТОЯЩЕМ SDK.

    Фейк примет что угодно, поэтому сам по себе он совместимости не доказывает.
    Этот тест ловит ровно тот класс ошибки, на котором адаптер и споткнулся при
    написании: ``temperature`` в ``anthropic`` 1.7.0 у ``messages.create`` НЕТ,
    ``**kwargs`` метод не принимает — вызов упал бы ``TypeError`` до сети, а
    широкий ``except`` выдал бы «network» на каждом посте. Тест дешёвый и
    сработает сам, если форма API снова поедет.
    """
    import inspect

    from anthropic.resources.messages import Messages

    allowed = set(inspect.signature(Messages.create).parameters)
    seen = _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    ac.chat(user="пост", system="постулаты", max_tokens=500, temperature=0.2)
    call = [s for s in seen if "__client__" not in s][0]
    unknown = set(call) - allowed
    assert not unknown, f"шлём в SDK несуществующие параметры: {sorted(unknown)}"


def test_temperature_never_sent(monkeypatch):
    """Температура игнорируется намеренно — SDK её не принимает (см. докстринг)."""
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    seen = _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    ac.chat(user="привет", temperature=0.2)
    call = [s for s in seen if "__client__" not in s][0]
    assert "temperature" not in call
    assert call["model"] == "claude-sonnet-5", "модель из env не доехала"


def test_error_attributes_match_real_sdk():
    """``status_code`` и ``message`` — реальные атрибуты, а не выдумка фейка."""
    import anthropic
    import httpx2

    resp = httpx2.Response(402, request=httpx2.Request("POST", "https://api.anthropic.com/"))
    err = anthropic.APIStatusError("Payment Required", response=resp, body=None)
    assert err.status_code == 402
    assert err.message == "Payment Required"
    assert issubclass(anthropic.APITimeoutError, anthropic.APIConnectionError)


# ───────── префикс-кэш ─────────


def test_system_block_is_marked_for_cache(monkeypatch):
    """Без cache_control кэш у Anthropic не включается — постулаты платятся заново."""
    seen = _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    ac.chat(user="пост", system="постулаты" * 100)
    call = [s for s in seen if "__client__" not in s][0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][0]["text"].startswith("постулаты")


def test_no_system_block_when_empty(monkeypatch):
    seen = _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    ac.chat(user="пост", system="   ")
    assert "system" not in [s for s in seen if "__client__" not in s][0]


# ───────── контракт отказов ─────────


def test_missing_sdk_is_a_reason_not_a_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import → ImportError
    out = ac.chat(user="привет")
    assert out == {"ok": False, "reason": "no_sdk", "detail": "pip install anthropic"}


def test_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    assert ac.chat(user="привет")["reason"] == "no_api_key"


def test_empty_prompt(monkeypatch):
    _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")]))
    assert ac.chat(user="   ")["reason"] == "empty_prompt"


def test_http_status_is_passed_through(monkeypatch):
    """402 (оплата), 401 (ключ) и 429 (квота) — разные решения, код нужен сырым."""
    _fake_sdk(monkeypatch, raises=_APIStatusError(402, "Payment Required"))
    out = ac.chat(user="привет")
    assert out["reason"] == "http_402"
    assert "Payment" in out["detail"]


def test_connection_error_is_network(monkeypatch):
    _fake_sdk(monkeypatch, raises=_APIConnectionError("боксу плохо"))
    assert ac.chat(user="привет")["reason"] == "network"


def test_truncated_checked_before_empty(monkeypatch):
    """G334: обрезка — отдельная поломка, лечится бюджетом, а не повтором."""
    _fake_sdk(monkeypatch, response=_Response([], stop_reason="max_tokens"))
    out = ac.chat(user="привет", max_tokens=777)
    assert out["reason"] == "truncated"
    assert out["detail"] == "max_tokens=777"


def test_empty_response(monkeypatch):
    _fake_sdk(monkeypatch, response=_Response([]))
    assert ac.chat(user="привет")["reason"] == "empty_response"


def test_non_text_blocks_are_skipped(monkeypatch):
    """Ответ — список блоков; thinking и прочее в контент не попадает."""
    blocks = [_Block("thinking", "рассуждения"), _Block("text", '{"verdicts": []}')]
    _fake_sdk(monkeypatch, response=_Response(blocks))
    assert ac.chat(user="привет")["content"] == '{"verdicts": []}'


def test_usage_line_survives_missing_fields(monkeypatch, caplog):
    """Учёт не важнее ответа: кривой usage не должен ронять вызов."""

    class _Bare:
        pass

    _fake_sdk(monkeypatch, response=_Response([_Block("text", "ok")], usage=_Bare()))
    with caplog.at_level("INFO", logger="modules.anthropic_client"):
        out = ac.chat(user="привет")
    assert out["ok"] is True
    assert "hit_pct=-" in caplog.text, "нет полей ≠ ноль — различие обязано быть видно"


def test_usage_line_reports_cache_share(monkeypatch, caplog):
    _fake_sdk(
        monkeypatch,
        response=_Response([_Block("text", "ok")], usage=_Usage(fresh=100, read=900, write=0)),
    )
    with caplog.at_level("INFO", logger="modules.anthropic_client"):
        ac.chat(user="привет", label="headless")
    assert "hit_pct=90.0" in caplog.text
    assert "label=headless" in caplog.text
