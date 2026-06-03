"""Tests for utils.json_logging — stdlib JSON log formatter + opt-in switch.

Покрывают: формат записи (валидный JSON, базовые поля), проброс exception и
пользовательского extra=..., и поведение env-тумблера LOG_FORMAT=json в
configure_json_logging / json_logging_enabled.
"""

from __future__ import annotations

import json
import logging

import pytest

from utils.json_logging import JSONFormatter, configure_json_logging, json_logging_enabled


def _make_record(**kwargs) -> logging.LogRecord:
    defaults = dict(
        name="setka.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    defaults.update(kwargs)
    return logging.LogRecord(func=None, **defaults)


def test_formatter_emits_valid_json_with_core_fields():
    out = JSONFormatter().format(_make_record())
    payload = json.loads(out)  # не должно бросать
    assert payload["level"] == "INFO"
    assert payload["logger"] == "setka.test"
    assert payload["message"] == "hello world"  # args отрендерены
    assert payload["ts"].endswith("Z")


def test_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = _make_record(level=logging.ERROR, msg="failed", args=(), exc_info=sys.exc_info())
    payload = json.loads(JSONFormatter().format(rec))
    assert "exception" in payload
    assert "ValueError" in payload["exception"]


def test_formatter_passes_through_extra_fields():
    rec = _make_record(msg="x", args=())
    rec.region_code = "mi"  # имитируем logger.info(..., extra={"region_code": "mi"})
    rec.count = 7
    payload = json.loads(JSONFormatter().format(rec))
    assert payload["region_code"] == "mi"
    assert payload["count"] == 7


def test_formatter_handles_unserializable_extra():
    rec = _make_record(msg="x", args=())
    rec.obj = object()  # не JSON-сериализуемо → repr-фолбэк, не падаем
    payload = json.loads(JSONFormatter().format(rec))
    assert "obj" in payload
    assert isinstance(payload["obj"], str)


def test_json_logging_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    assert json_logging_enabled() is False
    assert configure_json_logging() is False


@pytest.mark.parametrize("value", ["json", "JSON", " Json "])
def test_json_logging_enabled_via_env(value, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", value)
    assert json_logging_enabled() is True


def test_configure_installs_json_formatter(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        root.handlers = [logging.StreamHandler()]
        applied = configure_json_logging()
        assert applied is True
        assert all(isinstance(h.formatter, JSONFormatter) for h in root.handlers)
    finally:
        root.handlers = saved
