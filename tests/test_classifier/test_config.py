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
        assert cfg.get_source_days() == 1  # дефолт «только свежее» (решение владельца 2026-08-19)
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "7"}):
        assert cfg.get_source_days() == 7
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "999"}):
        assert cfg.get_source_days() == 30  # верхняя граница
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "0"}):
        assert cfg.get_source_days() == 1  # нижняя граница
    with patch.dict(os.environ, {"CLASSIFIER_SOURCE_DAYS": "junk"}):
        assert cfg.get_source_days() == 1


def test_read_postulates_nonempty():
    # Файл в репо есть — должен читаться и содержать заголовок.
    text = cfg.read_postulates()
    assert "Классификационные постулаты" in text


def test_rating_views_alpha_reads_env_and_falls_back():
    """Показатель степени берётся из env, но только разумный.

    Функция вся построена на «битый env не роняет отбор»: она вызывается
    внутри волны публикации, и исключение здесь стоило бы волны. Поэтому
    негодное значение читается как дефолт — включая ``nan``/``inf``, которые
    ``float()`` принимает молча, и отрицательные, переворачивающие смысл
    формулы. ``nan`` особенно тих: все сравнения с ним ложны, и сортировка
    витрины перестала бы упорядочивать что-либо без единой ошибки в логе.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("RATING_VIEWS_ALPHA", None)
        assert cfg.get_rating_views_alpha() == cfg.RATING_VIEWS_ALPHA_DEFAULT
    with patch.dict(os.environ, {"RATING_VIEWS_ALPHA": "0.35"}):
        assert cfg.get_rating_views_alpha() == 0.35
    for edge in ("0", "1", "0.0", "1.0"):
        with patch.dict(os.environ, {"RATING_VIEWS_ALPHA": edge}):
            assert cfg.get_rating_views_alpha() == float(edge), edge
    for bad in ("junk", "", "  ", "nan", "NaN", "inf", "-inf", "-0.5", "1.5", "1e9"):
        with patch.dict(os.environ, {"RATING_VIEWS_ALPHA": bad}):
            assert cfg.get_rating_views_alpha() == cfg.RATING_VIEWS_ALPHA_DEFAULT, bad
