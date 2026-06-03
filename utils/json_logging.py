"""Structured JSON logging — stdlib-only formatter.

Why: Celery worker (и при желании web) пишут plain-text — на инцидентах их
неудобно грепать/парсить (PENDING 🟢 Observability). Этот модуль даёт лёгкий
``JSONFormatter`` без внешних зависимостей (``python-json-logger`` НЕ нужен —
не тянем новый пакет на прод) и хелпер ``configure_json_logging`` для разовой
переустановки форматтера на root-логгере.

Включается опционально через env ``LOG_FORMAT=json`` (дефолт — текст), поэтому
поведение прода не меняется, пока владелец явно не выставит переменную.

Каждая запись — одна строка JSON c полями:
``ts`` (ISO-8601 UTC), ``level``, ``logger``, ``message`` и (если есть)
``exception``. ``extra={...}`` из вызова ``logger.info(..., extra=...)``
прозрачно попадает в JSON (всё, что не стандартные атрибуты LogRecord).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os

# Стандартные атрибуты LogRecord — всё остальное считаем пользовательским
# ``extra`` и кладём в JSON как есть. Список зафиксирован в CPython.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JSONFormatter(logging.Formatter):
    """Форматирует ``LogRecord`` в одну строку JSON (UTF-8, без ASCII-escape)."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 (logging API)
        payload = {
            "ts": _dt.datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Пользовательские поля из extra=... (не перетирают базовые ключи).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload:
                continue
            try:
                json.dumps(value, default=str)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        return json.dumps(payload, ensure_ascii=False, default=str)


def json_logging_enabled() -> bool:
    """True, если env ``LOG_FORMAT`` (case-insensitive) равна ``json``."""
    return os.getenv("LOG_FORMAT", "").strip().lower() == "json"


def configure_json_logging(level: int | None = None) -> bool:
    """Переустановить root-логгер на ``JSONFormatter``, если включён JSON-режим.

    Идемпотентна: заменяет форматтер у существующих хендлеров root-логгера (а
    если их нет — добавляет один ``StreamHandler``). Возвращает True, если
    JSON-режим был применён, иначе False (тогда логирование остаётся как было).

    ``level`` — опциональный override уровня root-логгера.
    """
    if not json_logging_enabled():
        return False

    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)

    formatter = JSONFormatter()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return True
