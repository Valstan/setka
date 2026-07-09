"""Тесты обвязки облачной рутины классификатора (scripts/classifier_routine.py).

Покрывают чистую логику: валидацию пакета вердиктов (то, что модель чаще всего
портит в JSON) и выбор базового URL из env. HTTP-функции (`fetch`/`submit`)
не трогаем — они интеграционные, живут против прод-API.
"""

from scripts.classifier_routine import DEFAULT_API_BASE, api_base, validate_verdicts


def _verdict(**overrides):
    base = {
        "lip": "156168183_14260",
        "theme": "новости района",
        "action": "publish",
        "merge_with": [],
        "split": False,
        "confidence": 90,
        "reasoning": "локальное событие",
        "text": "т",
        "url": "https://vk.com/wall-1_1",
        "region_code": "mi",
    }
    base.update(overrides)
    return base


def test_valid_batch_passes():
    payload = {"verdicts": [_verdict(), _verdict(lip="1_2", action="hold")]}
    assert validate_verdicts(payload) == []


def test_not_a_dict_and_empty_list_rejected():
    assert validate_verdicts([_verdict()]) == ['ожидается объект {"verdicts": [...]}']
    assert validate_verdicts({"verdicts": []}) == ["пустой список verdicts — нечего отправлять"]


def test_missing_lip_and_bad_action_reported_with_index():
    payload = {"verdicts": [_verdict(lip=""), _verdict(action="drop")]}
    errors = validate_verdicts(payload)
    assert any(e.startswith("[0]") and "lip" in e for e in errors)
    assert any(e.startswith("[1]") and "action" in e for e in errors)


def test_confidence_bounds_and_bool_rejected():
    assert validate_verdicts({"verdicts": [_verdict(confidence=101)]})
    assert validate_verdicts({"verdicts": [_verdict(confidence=True)]})
    assert validate_verdicts({"verdicts": [_verdict(confidence=None)]}) == []


def test_merge_with_must_be_list():
    assert validate_verdicts({"verdicts": [_verdict(merge_with="1_2")]})


def test_api_base_env_override_strips_trailing_slash(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_API_BASE", raising=False)
    assert api_base() == DEFAULT_API_BASE
    monkeypatch.setenv("CLASSIFIER_API_BASE", "https://example.test/")
    assert api_base() == "https://example.test"
