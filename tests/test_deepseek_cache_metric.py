"""Гейт: доля префикс-кэша DeepSeek измеряется и различима по потребителям.

Мандат brain 2026-08-30 (R29, распоряжение владельца): конвейерные запросы к
DeepSeek структурируются под автоматическое префикс-кэширование. Кэш включён у
провайдера по умолчанию и работает, пока НАЧАЛО запроса байт-в-байт совпадает с
недавним; ничего не настраивается — но и не сигналит.

**Почему это надо мерить, а не проверять глазами.** Сломать стабильность
префикса может правка, которая выглядит совершенно безобидно: сортировка правил
без вторичного ключа, добавленная в шаблон дата, переставленный ключ словаря.
При этом ответы модели остаются правильными, вызовы проходят, в логах ни одной
ошибки — меняется ТОЛЬКО счёт. Единственный наблюдаемый сигнал, что префикс
поехал, — упавшая доля кэша. Без строки учёта отказ невидим до счёта за месяц.

**Почему ``hit=-`` и ``hit=0`` — разные вещи.** Первое значит «провайдер полей
не вернул, померить нечем», второе — «померили, кэш не сработал». Свести их в
один ноль значит получить приёмку, которая врёт в обе стороны: молчащая метрика
покажется провалом кэша, а настоящий провал утонет среди «нет данных». Отдельный
тест держит эту границу (pool #229 — вердикт выносит независимое чтение
состояния, а не удобная интерпретация).

**Почему метка обязательна.** Потребителей пять, и они разного класса: конвейер
шлёт тысячи однотипных вызовов (доля кэша обязана быть высокой), а черновик
ответа в UI зовут пару раз в день с уникальным текстом (доля будет низкой, и это
нормально). Сложенные в одно среднее, они не значат ничего.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import List, Tuple

import pytest

from modules.deepseek_client import log_cache_usage

REPO_ROOT = Path(__file__).resolve().parent.parent

# Модули, которые ходят в DeepSeek. Появился шестой — его сюда, вместе с меткой.
CONSUMERS = [
    ("modules/classifier/headless.py", "headless"),
    ("modules/classifier/distill.py", "distill"),
    ("modules/conveyor/classify.py", "conveyor"),
    ("modules/discovery/ai_categorizer.py", "discovery"),
    ("modules/notifications/ai_drafter.py", "ai_drafter"),
]


def _line(caplog) -> str:
    lines = [r.getMessage() for r in caplog.records if "deepseek-usage" in r.getMessage()]
    assert len(lines) == 1, f"ожидалась одна строка учёта, получено {len(lines)}: {lines}"
    return lines[0]


def test_hit_share_is_computed_from_the_providers_own_numbers(caplog):
    caplog.set_level(logging.INFO, logger="modules.deepseek_client")
    log_cache_usage(
        "headless",
        {
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 20000,
                "prompt_cache_hit_tokens": 19000,
                "prompt_cache_miss_tokens": 1000,
                "completion_tokens": 900,
            },
        },
    )
    line = _line(caplog)

    assert "label=headless" in line
    assert "hit=19000" in line
    assert "miss=1000" in line
    assert "hit_pct=95.0" in line, f"доля посчитана неверно: {line}"


def test_missing_fields_are_reported_as_unknown_not_as_zero(caplog):
    """``hit=-`` значит «померить нечем», а не «кэш не сработал»."""
    caplog.set_level(logging.INFO, logger="modules.deepseek_client")
    log_cache_usage("discovery", {"model": "deepseek-chat", "usage": {"prompt_tokens": 500}})
    line = _line(caplog)

    assert "hit=-" in line and "miss=-" in line, f"отсутствие полей выдано за данные: {line}"
    assert "hit_pct=-" in line, f"доля посчитана из ничего: {line}"
    assert "hit=0" not in line, "«нет данных» подменено нулём — приёмка стала бы врать"


def test_a_genuine_cache_miss_is_reported_as_zero_not_as_unknown(caplog):
    """Обратная граница: кэш действительно не сработал — это данные, а не их отсутствие."""
    caplog.set_level(logging.INFO, logger="modules.deepseek_client")
    log_cache_usage(
        "conveyor",
        {
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 3000,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 3000,
            },
        },
    )
    line = _line(caplog)

    assert "hit=0" in line, f"настоящий промах выдан за отсутствие данных: {line}"
    assert "hit_pct=0.0" in line, f"{line}"


def test_metric_never_breaks_an_already_paid_call(caplog):
    """Вызов оплачен — ответ важнее учёта."""
    caplog.set_level(logging.INFO, logger="modules.deepseek_client")
    log_cache_usage("headless", {"usage": "не словарь, а строка"})
    log_cache_usage("headless", {})
    assert not [r for r in caplog.records if "deepseek-usage" in r.getMessage()]


def _labelled_calls(path: Path) -> List[Tuple[str, str]]:
    """Вызовы chat()/call_api() в модуле и значение их аргумента ``label``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[Tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in {"chat", "call_api", "_call_api"}:
            continue
        # asyncio.to_thread(chat, ...) — chat передан аргументом, а не вызван.
        label = ""
        for kw in node.keywords:
            if kw.arg == "label" and isinstance(kw.value, ast.Constant):
                label = str(kw.value.value)
        found.append((name, label))
    # to_thread(chat, user=..., label=...) — вызов to_thread, но kwargs его же.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "to_thread" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id in {"chat", "call_api"}:
                    label = ""
                    for kw in node.keywords:
                        if kw.arg == "label" and isinstance(kw.value, ast.Constant):
                            label = str(kw.value.value)
                    found.append((first.id, label))
    return found


@pytest.mark.parametrize("rel_path,expected_label", CONSUMERS)
def test_every_consumer_labels_its_calls(rel_path: str, expected_label: str):
    calls = _labelled_calls(REPO_ROOT / rel_path)
    assert calls, f"в {rel_path} не нашлось вызовов DeepSeek — список CONSUMERS устарел"

    unlabelled = [name for name, label in calls if not label]
    assert not unlabelled, (
        f"{rel_path}: вызов {unlabelled} идёт без label — его расход кэша сольётся "
        "с чужим, и по средней доле нельзя будет понять, чей префикс поехал."
    )
    labels = {label for _, label in calls}
    assert labels == {
        expected_label
    }, f"{rel_path}: ожидалась метка {expected_label!r}, нашлось {labels}"
