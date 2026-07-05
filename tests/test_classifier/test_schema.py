"""Tests схемы вердикта (ADR-0003 §B)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.classifier.schema import ClassifierVerdict


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
    }
