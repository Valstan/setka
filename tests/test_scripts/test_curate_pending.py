"""Тесты CLI shadow-курации (scripts/curate_pending.py).

Покрывают чистую join-логику `_flagged_from_run` — выгрузку drop-постов для
precision-спот-чека владельцем (gate Фазы 2). БД-функции (`_list`/`_stats`/
`_flagged`) тут не трогаем — они интеграционные.
"""

# Регистрируем оба модуля моделей (как в test_curation_recorder) — иначе
# SQLAlchemy-mapper падает на relationship при импорте скрипта.
import database.models  # noqa: F401
import database.models_extended  # noqa: F401
from scripts.curate_pending import _flagged_from_run


def _cands():
    return [
        {
            "lip": "1_10",
            "url": "https://vk.com/wall1_10",
            "text": "район: ярмарка",
            "has_media": True,
        },
        {
            "lip": "1_11",
            "url": "https://vk.com/wall1_11",
            "text": "реклама авто",
            "has_media": False,
        },
        {
            "lip": "1_12",
            "url": "https://vk.com/wall1_12",
            "text": "федерал МРОТ",
            "has_media": False,
        },
    ]


def test_flagged_only_drops_with_text_and_url():
    verdicts = [
        {"lip": "1_10", "verdict": "keep", "reason": ""},
        {"lip": "1_11", "verdict": "drop", "reason": "реклама: продажа авто"},
        {"lip": "1_12", "verdict": "drop", "reason": "федеральный: без привязки"},
    ]
    out = _flagged_from_run(_cands(), verdicts)
    assert [f["lip"] for f in out] == ["1_11", "1_12"]
    assert out[0]["url"] == "https://vk.com/wall1_11"
    assert out[0]["text"] == "реклама авто"
    assert out[0]["reason"] == "реклама: продажа авто"
    assert out[0]["has_media"] is False


def test_flagged_missing_candidate_degrades_not_drops_row():
    # drop с lip, которого нет в candidates → строка остаётся, поля пустые
    verdicts = [{"lip": "9_99", "verdict": "drop", "reason": "осиротевший drop"}]
    out = _flagged_from_run(_cands(), verdicts)
    assert len(out) == 1
    assert out[0]["lip"] == "9_99"
    assert out[0]["url"] is None
    assert out[0]["text"] == ""
    assert out[0]["reason"] == "осиротевший drop"


def test_flagged_empty_and_none_inputs():
    assert _flagged_from_run(None, None) == []
    assert _flagged_from_run([], []) == []
    # вердикты есть, но все keep → пусто
    assert _flagged_from_run(_cands(), [{"lip": "1_10", "verdict": "keep"}]) == []


def test_flagged_ignores_malformed_entries():
    verdicts = [
        "not-a-dict",
        {"lip": "1_11", "verdict": "drop", "reason": "  пробелы по краям  "},
    ]
    out = _flagged_from_run(_cands(), verdicts)
    assert len(out) == 1
    assert out[0]["reason"] == "пробелы по краям"  # .strip() применён
