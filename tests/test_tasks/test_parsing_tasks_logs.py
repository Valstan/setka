"""Tests for SETKA_LOGS_DIR env in tasks/parsing_tasks.py.

Проверяем что:
- без env используется прод-дефолт `/home/valstan/SETKA/logs`
- с env SETKA_LOGS_DIR= все парсер-пути идут от него (OUTPUT_DIR,
  REPORTS_DIR, VIDEO_REPORT_PATH, parser.log)
- _init_logger safe-fallback'ит на StreamHandler при недоступном пути,
  не падает на import (это важно для pytest --collect-only)

Тесты используют importlib.reload, потому что константы вычисляются
на module-import. После каждого теста — cleanup: убрать handlers
у logger 'vk_parser' и reload module с чистым env, чтобы не отравить
state другим тестам.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def reload_parsing_tasks() -> Iterator:
    """Reload `tasks.parsing_tasks` после изменения env и очистить
    state логгера. После теста — reload ещё раз с дефолтным env."""
    # 1) Сбросить handlers логгера, чтобы _init_logger перенастроил.
    logger = logging.getLogger("vk_parser")
    saved_handlers = list(logger.handlers)
    logger.handlers.clear()

    yield  # тест меняет env и делает importlib.reload сам

    # 2) Восстановить: убрать всё что добавили в этом тесте, reload
    # с чистым env, чтобы дефолтные константы вернулись.
    logger.handlers.clear()
    os.environ.pop("SETKA_LOGS_DIR", None)
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)
    # restore original handlers (если кто-то держал ссылку)
    for h in saved_handlers:
        if h not in logger.handlers:
            logger.addHandler(h)


def test_default_setka_logs_dir_when_env_missing(reload_parsing_tasks, monkeypatch):
    """Без SETKA_LOGS_DIR используется прод-дефолт."""
    monkeypatch.delenv("SETKA_LOGS_DIR", raising=False)
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)

    assert parsing_tasks.SETKA_LOGS_DIR == "/home/valstan/SETKA/logs"
    assert parsing_tasks.OUTPUT_DIR == os.path.join("/home/valstan/SETKA/logs", "parser")
    assert parsing_tasks.REPORTS_DIR == os.path.join(
        "/home/valstan/SETKA/logs", "parser", "reports"
    )
    assert parsing_tasks.VIDEO_REPORT_PATH == os.path.join(
        "/home/valstan/SETKA/logs", "parser_video_report.log"
    )


def test_env_overrides_setka_logs_dir(reload_parsing_tasks, monkeypatch, tmp_path):
    """SETKA_LOGS_DIR=<tmp_path> — все 4 пути идут от него."""
    monkeypatch.setenv("SETKA_LOGS_DIR", str(tmp_path))
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)

    assert parsing_tasks.SETKA_LOGS_DIR == str(tmp_path)
    assert parsing_tasks.OUTPUT_DIR == os.path.join(str(tmp_path), "parser")
    assert parsing_tasks.REPORTS_DIR == os.path.join(str(tmp_path), "parser", "reports")
    assert parsing_tasks.VIDEO_REPORT_PATH == os.path.join(str(tmp_path), "parser_video_report.log")


def test_init_logger_creates_file_handler_on_writable_dir(
    reload_parsing_tasks, monkeypatch, tmp_path
):
    """При writable SETKA_LOGS_DIR — _init_logger ставит FileHandler
    и создаёт saб-папку."""
    monkeypatch.setenv("SETKA_LOGS_DIR", str(tmp_path))
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)

    handlers = parsing_tasks.logger.handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.FileHandler)
    # SETKA_LOGS_DIR должна быть создана (os.makedirs exist_ok=True)
    assert Path(str(tmp_path)).is_dir()
    # parser.log файл создаётся при первой записи, а не при FileHandler() —
    # достаточно убедиться, что путь именно туда.
    assert handlers[0].baseFilename == os.path.join(str(tmp_path), "parser.log")


def test_init_logger_falls_back_to_stderr_on_unwritable_path(
    reload_parsing_tasks, monkeypatch, tmp_path
):
    """Если SETKA_LOGS_DIR недоступен (например, путь = поддиректория
    существующего файла) — _init_logger не падает, ставит StreamHandler."""
    # Кросс-платформенный способ заставить os.makedirs упасть:
    # создаём обычный файл и пытаемся использовать его как родителя.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("blocker", encoding="utf-8")
    bad_path = str(blocker / "logs")  # os.makedirs упадёт с NotADirectoryError
    monkeypatch.setenv("SETKA_LOGS_DIR", bad_path)
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)

    handlers = parsing_tasks.logger.handlers
    assert len(handlers) == 1
    # FileHandler наследует StreamHandler, поэтому проверяем строго
    # type(...) is StreamHandler.
    assert type(handlers[0]) is logging.StreamHandler  # noqa: E721


def test_logger_is_idempotent_when_handlers_already_set(
    reload_parsing_tasks, monkeypatch, tmp_path
):
    """Повторный вызов _init_logger не добавляет дубль-handler."""
    monkeypatch.setenv("SETKA_LOGS_DIR", str(tmp_path))
    from tasks import parsing_tasks  # noqa: WPS433

    importlib.reload(parsing_tasks)
    handlers_before = list(parsing_tasks.logger.handlers)
    assert len(handlers_before) == 1

    # Прямой повторный вызов — handlers не должны дублироваться.
    again = parsing_tasks._init_logger()
    assert again is parsing_tasks.logger
    assert parsing_tasks.logger.handlers == handlers_before
