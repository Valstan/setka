"""Tests схемы вердикта (ADR-0003 §B)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.classifier.schema import ClassifierVerdict, parse_verdict_loose


def test_normalizes_bad_action_to_hold():
    v = ClassifierVerdict(lip="1_1", theme="novost", action="БОЛТ")
    assert v.normalized_action() == "hold"
    assert v.to_verdict_json()["action"] == "hold"


def test_valid_action_kept():
    v = ClassifierVerdict(lip="1_1", theme="reklama", action="delete")
    assert v.to_verdict_json()["action"] == "delete"


def test_merge_signal():
    assert ClassifierVerdict(lip="1_1", theme="t", merge_with=["1_2", "1_3"]).has_merge_signal()
    assert ClassifierVerdict(lip="1_1", theme="t", split=True).has_merge_signal()
    assert not ClassifierVerdict(lip="1_1", theme="t").has_merge_signal()


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        ClassifierVerdict(lip="1_1", theme="t", confidence=150)


def test_empty_theme_rejected():
    with pytest.raises(ValidationError):
        ClassifierVerdict(lip="1_1", theme="")


def test_to_verdict_json_shape():
    v = ClassifierVerdict(
        lip="1_9",
        theme="  спорт ",
        action="publish",
        merge_with=["1_2"],
        confidence=80,
        reasoning="  матч  ",
    )
    j = v.to_verdict_json()
    assert j == {
        "theme": "спорт",
        "action": "publish",
        "merge_with": ["1_2"],
        "split": False,
        "confidence": 80,
        "reasoning": "матч",
        # `urgent` пишется всегда, в том числе False: читатель вердиктов должен
        # отличать «модель сказала не срочно» от «поля нет, вердикт старый».
        # `importance` — наоборот, только когда задана.
        "urgent": False,
    }


def test_urgent_defaults_to_false_on_an_old_verdict():
    """Вердикты, записанные до 2026-09-22, поля `urgent` не имеют.

    Разбор обязан читать их как «не срочно», а не падать: в БД таких записей
    десятки тысяч, и они по-прежнему участвуют в отборе.
    """
    v = parse_verdict_loose({"lip": "1_1", "theme": "новости", "action": "publish"})

    assert v is not None
    assert v.urgent is False
    assert v.importance is None


def test_urgent_accepts_the_string_forms_llm_actually_returns():
    """Модель отдаёт то булев true, то строку — это её обычное поведение."""
    for raw in ("true", "True", "да", "yes", "1"):
        v = parse_verdict_loose({"lip": "1_1", "theme": "т", "urgent": raw})
        assert v is not None and v.urgent is True, raw

    for raw in ("false", "нет", "", "не знаю", None):
        v = parse_verdict_loose({"lip": "1_1", "theme": "т", "urgent": raw})
        assert v is not None and v.urgent is False, raw


def test_importance_is_clamped_and_garbage_becomes_none():
    assert parse_verdict_loose({"lip": "1_1", "theme": "т", "importance": 500}).importance == 100
    assert parse_verdict_loose({"lip": "1_1", "theme": "т", "importance": -7}).importance == 0
    assert parse_verdict_loose({"lip": "1_1", "theme": "т", "importance": "ой"}).importance is None


def test_importance_reaches_the_verdict_json_only_when_set():
    with_value = ClassifierVerdict(lip="1_1", theme="т", importance=42).to_verdict_json()
    without = ClassifierVerdict(lip="1_1", theme="т").to_verdict_json()

    assert with_value["importance"] == 42
    assert "importance" not in without
