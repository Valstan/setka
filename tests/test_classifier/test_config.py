"""Tests конфига классификатора."""

from __future__ import annotations

import os
from unittest.mock import patch

from config import classifier as cfg


def test_ingest_key_stripped():
    with patch.dict(os.environ, {"CLASSIFIER_INGEST_KEY": "  secret  "}):
        assert cfg.get_ingest_key() == "secret"
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLASSIFIER_INGEST_KEY", None)
        assert cfg.get_ingest_key() == ""


def test_kill_switch():
    with patch.dict(os.environ, {"CLASSIFIER_DISABLED": "1"}):
        assert cfg.classifier_disabled() is True
    with patch.dict(os.environ, {"CLASSIFIER_DISABLED": "0"}):
        assert cfg.classifier_disabled() is False


def test_region_allowlist_csv_and_semicolon():
    with patch.dict(os.environ, {"CLASSIFIER_REGION_CODES": "mi, vp ;ur"}):
        assert cfg.get_region_allowlist() == ["mi", "vp", "ur"]
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLASSIFIER_REGION_CODES", None)
        assert cfg.get_region_allowlist() == []


def test_pending_max_bounds():
    with patch.dict(os.environ, {"CLASSIFIER_PENDING_MAX": "999"}):
        assert cfg.get_pending_max() == 200
    with patch.dict(os.environ, {"CLASSIFIER_PENDING_MAX": "junk"}):
        assert cfg.get_pending_max() == 40


def test_source_days_default_and_bounds():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLASSIFIER_SOURCE_DAYS", None)
        assert cfg.get_source_days() == 3  # дефолт «не старше 3 суток»
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "7"}):
        assert cfg.get_source_days() == 7
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "999"}):
        assert cfg.get_source_days() == 30  # верхняя граница
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "0"}):
        assert cfg.get_source_days() == 1  # нижняя граница
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "junk"}):
        assert cfg.get_source_days() == 3


def test_read_postulates_nonempty():
    # Файл в репо есть — должен читаться и содержать заголовок.
    text = cfg.read_postulates()
    assert "Классификационные постулаты" in text
