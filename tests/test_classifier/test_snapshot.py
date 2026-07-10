"""Tests снапшота выученных правил (хвост Б ADR-0005): конфиг, регистрация beat-джобы."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from config.classifier import get_rule_stale_days, get_rules_snapshot_path


def test_rule_stale_days_default_and_bounds():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLASSIFIER_RULE_STALE_DAYS", None)
        assert get_rule_stale_days() == 90
    with patch.dict(os.environ, {"CLASSIFIER_RULE_STALE_DAYS": "30"}):
        assert get_rule_stale_days() == 30
    with patch.dict(os.environ, {"CLASSIFIER_RULE_STALE_DAYS": "1"}):
        assert get_rule_stale_days() == 7  # нижняя граница
    with patch.dict(os.environ, {"CLASSIFIER_RULE_STALE_DAYS": "9999"}):
        assert get_rule_stale_days() == 365  # верхняя граница
    with patch.dict(os.environ, {"CLASSIFIER_RULE_STALE_DAYS": "junk"}):
        assert get_rule_stale_days() == 90


def test_snapshot_path_default_and_override():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLASSIFIER_RULES_SNAPSHOT_PATH", None)
        assert get_rules_snapshot_path() == Path("logs") / "classifier_learned_rules_snapshot.md"
    with patch.dict(os.environ, {"CLASSIFIER_RULES_SNAPSHOT_PATH": "/tmp/snap.md"}):
        assert get_rules_snapshot_path() == Path("/tmp/snap.md")


def test_snapshot_task_and_beat_registered():
    from tasks.celery_app import app, snapshot_learned_rules  # noqa: F401

    assert "tasks.celery_app.snapshot_learned_rules" in app.tasks
    assert "snapshot-learned-rules-daily" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["snapshot-learned-rules-daily"]
    assert entry["task"] == "tasks.celery_app.snapshot_learned_rules"
