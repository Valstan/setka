"""Прибор памяти: какая задача поднимает потолок RSS дочернего процесса.

P164 оставил про течь единственное знание — окно HH:17–19, то есть догадку по
соседству с расписанием. Оба порога переработки ребёнка лечат накопление, но
виновника не называют. Хук печатает имя задачи в момент роста пика.

Тесты держат три обещания: рост печатается с именем задачи, отсутствие роста
молчит (иначе строка повторялась бы после каждой задачи и утонула бы в логе),
и отказ самого прибора не трогает задачу — на Windows-машине разработчика
модуля ``resource`` нет вовсе.
"""

from __future__ import annotations

import sys
import types

KB = 1024


def _fake_resource(peak_kb: int) -> types.ModuleType:
    mod = types.ModuleType("resource")
    mod.RUSAGE_SELF = 0
    mod.getrusage = lambda _who: types.SimpleNamespace(ru_maxrss=peak_kb)
    return mod


def _task(name: str):
    return types.SimpleNamespace(name=name)


def test_growth_is_logged_with_the_task_name(monkeypatch, caplog):
    from tasks import celery_app as mod

    monkeypatch.setattr(mod, "_last_peak_rss_kb", 100 * KB)
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(200 * KB))

    with caplog.at_level("INFO", logger="tasks.celery_app"):
        mod._setka_log_peak_rss(task=_task("tasks.celery_app.check_recent_comments"))

    line = next((r.getMessage() for r in caplog.records if "mem: task=" in r.getMessage()), None)
    assert line is not None, "рост пика RSS не попал в лог"
    assert "check_recent_comments" in line
    assert "peak_rss_mb=200" in line
    assert "delta_mb=100" in line


def test_small_step_below_the_floor_stays_quiet(monkeypatch, caplog):
    """Рост на 1 МБ у небольшого процесса — шум, а не сигнал."""
    from tasks import celery_app as mod

    monkeypatch.setattr(mod, "_last_peak_rss_kb", 100 * KB)
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(101 * KB))

    with caplog.at_level("INFO", logger="tasks.celery_app"):
        mod._setka_log_peak_rss(task=_task("tasks.celery_app.dispatch_broadcasts"))

    assert not [r for r in caplog.records if "mem: task=" in r.getMessage()]


def test_no_growth_is_not_logged_even_above_the_floor(monkeypatch, caplog):
    """ru_maxrss монотонен: без ступени роста печатать нечего.

    Без этой проверки выше порога пола строка повторялась бы с одним и тем же
    числом после каждой задачи — прибор сам себя утопил бы в логе.
    """
    from tasks import celery_app as mod

    monkeypatch.setattr(mod, "_last_peak_rss_kb", 400 * KB)
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(400 * KB))

    with caplog.at_level("INFO", logger="tasks.celery_app"):
        mod._setka_log_peak_rss(task=_task("tasks.celery_app.dispatch_broadcasts"))

    assert not [r for r in caplog.records if "mem: task=" in r.getMessage()]


def test_any_growth_above_the_floor_is_logged(monkeypatch, caplog):
    """Выше 300 МБ интересна каждая ступень — там ищут виновника."""
    from tasks import celery_app as mod

    monkeypatch.setattr(mod, "_last_peak_rss_kb", 310 * KB)
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(312 * KB))

    with caplog.at_level("INFO", logger="tasks.celery_app"):
        mod._setka_log_peak_rss(task=_task("tasks.celery_app.parse_and_publish_theme"))

    assert any("peak_rss_mb=312" in r.getMessage() for r in caplog.records)


def test_probe_without_resource_module_does_not_raise(monkeypatch):
    """На Windows ``resource`` отсутствует — прибор обязан промолчать."""
    from tasks import celery_app as mod

    monkeypatch.setitem(sys.modules, "resource", None)

    mod._setka_log_peak_rss(task=_task("tasks.celery_app.anything"))


def test_probe_survives_a_task_without_a_name(monkeypatch, caplog):
    from tasks import celery_app as mod

    monkeypatch.setattr(mod, "_last_peak_rss_kb", 0)
    monkeypatch.setitem(sys.modules, "resource", _fake_resource(500 * KB))

    with caplog.at_level("INFO", logger="tasks.celery_app"):
        mod._setka_log_peak_rss(task=None)

    assert any("task=unknown" in r.getMessage() for r in caplog.records)
