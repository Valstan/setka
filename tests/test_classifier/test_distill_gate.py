"""Гейт автоматической дистилляции — свой, а не одолженный у ИИ-фильтра.

До 2026-09-04 beat-слот ``classifier-distill-weekly`` гейтился
``CLASSIFIER_HEADLESS_ENABLED``. Докстринг задачи описывал развод веток («пока
дистилляция делается вручную из чата, две ветки предлагали бы правила по одному
и тому же сырью»), но флаг включает КЛАССИФИКАЦИЮ ПОТОКА и на проде поднят —
поэтому условие никогда не срабатывало, и робот всю дорогу чеканил правила
параллельно ручной дистилляции. За неделю панель набрала 8 черновиков, каждый
пересказывал уже утверждённое правило.

Тесты держат именно эту границу: у дистилляции собственный переключатель, и
поднятый флаг ИИ-фильтра её НЕ включает.
"""

from __future__ import annotations

import config.classifier as cfg
from tasks.celery_app import distill_classifier_rules


def test_distill_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_DISTILL_ENABLED", raising=False)
    assert cfg.distill_enabled() is False


def test_distill_flag_reads_usual_truthy_forms(monkeypatch):
    for raw in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CLASSIFIER_DISTILL_ENABLED", raw)
        assert cfg.distill_enabled() is True, raw
    for raw in ("0", "false", "", "нет"):
        monkeypatch.setenv("CLASSIFIER_DISTILL_ENABLED", raw)
        assert cfg.distill_enabled() is False, raw


def test_headless_flag_does_not_enable_distill(monkeypatch):
    """Ровно тот случай, что жил на проде: фильтр включён, дистилляция — нет."""
    monkeypatch.setenv("CLASSIFIER_HEADLESS_ENABLED", "1")
    monkeypatch.delenv("CLASSIFIER_DISTILL_ENABLED", raising=False)

    assert cfg.headless_enabled() is True
    assert cfg.distill_enabled() is False


def test_task_skips_while_distill_is_off(monkeypatch):
    """Задача при выключенном флаге не должна дойти до модели и записи черновиков."""
    monkeypatch.setattr("config.classifier.classifier_disabled", lambda: False, raising=False)
    monkeypatch.setattr("config.classifier.distill_enabled", lambda: False, raising=False)

    def _must_not_run(name):  # pragma: no cover — страховка от регресса
        raise AssertionError("дистилляция дошла до секретов при выключенном гейте")

    monkeypatch.setattr("modules.secrets_bootstrap.ensure_secret", _must_not_run)

    assert distill_classifier_rules() == {"status": "skipped:distill-off"}


def test_task_skips_when_classifier_disabled(monkeypatch):
    """Общий рубильник классификатора остаётся главнее частного флага."""
    monkeypatch.setattr("config.classifier.classifier_disabled", lambda: True, raising=False)
    monkeypatch.setattr("config.classifier.distill_enabled", lambda: True, raising=False)

    assert distill_classifier_rules() == {"status": "skipped:classifier-disabled"}
