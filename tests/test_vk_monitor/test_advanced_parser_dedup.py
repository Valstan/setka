"""Дедупликация в AdvancedVKParser (lip после unwrap, текст, медиа)."""

import time

import pytest

from modules.deduplication.fingerprints import (
    create_text_fingerprint,
    create_text_simhash,
    text_to_rafinad,
)
from modules.vk_monitor.advanced_parser import AdvancedVKParser


class _DummyVk:
    pass


def _fresh_post(owner_id: int, post_id: int, text: str, extra=None):
    now = int(time.time())
    p = {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "date": now,
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
    }
    if extra:
        p.update(extra)
    return p


@pytest.mark.asyncio
async def test_same_lip_twice_in_batch_second_rejected():
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()

    p = _fresh_post(-100, 555, "Новость одинакового источника " * 5)
    r1 = await parser._filter_post(
        p,
        "novost",
        None,
        [],
        set(),
        set(),
    )
    r2 = await parser._filter_post(
        dict(p),
        "novost",
        None,
        [],
        set(),
        set(),
    )
    assert r1 is not None
    assert r2 is None
    assert parser.stats["posts_filtered_duplicate_lip"] >= 1


@pytest.mark.asyncio
async def test_same_text_different_lip_second_rejected():
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()

    body = "Один и тот же текст новости для дедупа " * 4
    r1 = await parser._filter_post(
        _fresh_post(-1, 1, body),
        "novost",
        None,
        [],
        set(),
        set(),
    )
    r2 = await parser._filter_post(
        _fresh_post(-2, 2, body),
        "novost",
        None,
        [],
        set(),
        set(),
    )
    assert r1 is not None
    assert r2 is None
    assert parser.stats["posts_filtered_duplicate_text"] >= 1


@pytest.mark.asyncio
async def test_historical_text_fingerprint_rejected():
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    parser._historical_text_simhashes = []

    text = "Исторический дубль текста для проверки " * 3
    fp = create_text_fingerprint(text)
    r = await parser._filter_post(
        _fresh_post(-10, 10, text),
        "novost",
        None,
        [],
        {f"txtfp:{fp}"},
        set(),
    )
    assert r is None
    assert parser.stats["posts_filtered_duplicate_text"] >= 1


@pytest.mark.asyncio
async def test_historical_simhash_rejected_on_90_percent_similarity():
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    parser._text_similarity_threshold = 0.90
    parser._max_simhash_hamming = parser._compute_max_simhash_hamming(
        parser._text_similarity_threshold
    )
    parser._min_rafinad_similarity = 20

    base_text = "Срочно в районе пройдет мероприятие в эту субботу на центральной площади"
    near_text = "Срочно в районе пройдет мероприятие в эту субботу на центральной площади сегодня"
    bucket = len(text_to_rafinad(base_text)) // 20
    parser._historical_text_simhashes = [(bucket, create_text_simhash(base_text))]

    r = await parser._filter_post(
        _fresh_post(-11, 11, near_text),
        "novost",
        None,
        [],
        set(),
        set(),
    )
    assert r is None
    assert parser.stats["posts_filtered_duplicate_text"] >= 1


def _isolate_jaccard(parser, *, enabled=True, threshold=0.8, min_tokens=5):
    """Подготовить парсер так, чтобы near-dup ловил ТОЛЬКО Jaccard (SimHash
    отключён невозможным порогом Хэмминга), для детерминированного теста."""
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    parser._historical_text_simhashes = []
    parser._batch_token_sets = []
    parser._min_rafinad_similarity = 20
    parser._max_simhash_hamming = -1  # SimHash никогда не матчит
    parser._jaccard_enabled = enabled
    parser._jaccard_threshold = threshold
    parser._jaccard_min_tokens = min_tokens
    parser._simhash_bucket_gate = 5  # широкий гейт — длина пересказа гуляет


@pytest.mark.asyncio
async def test_jaccard_intrabatch_reordered_duplicate_rejected():
    """Переставленный/переписанный пересказ той же новости ловится Jaccard'ом,
    хотя char-SimHash его упускает (порядок слов другой)."""
    parser = AdvancedVKParser(_DummyVk())
    _isolate_jaccard(parser)

    base = (
        "В Кильмези в субботу откроется новая детская площадка рядом со школой "
        "номер три приходите всей семьёй на большой праздник"
    )
    reworded = (
        "Приходите всей семьёй на большой праздник в субботу в Кильмези рядом "
        "со школой номер три откроется новая детская площадка"
    )
    r1 = await parser._filter_post(_fresh_post(-1, 1, base), "novost", None, [], set(), set())
    r2 = await parser._filter_post(_fresh_post(-2, 2, reworded), "novost", None, [], set(), set())
    assert r1 is not None
    assert r2 is None
    assert parser.stats["near_dup_jaccard"] >= 1
    assert parser.stats["near_dup_simhash"] == 0


@pytest.mark.asyncio
async def test_jaccard_distinct_posts_not_rejected():
    """Две разные районные новости (низкое пересечение слов) обе проходят."""
    parser = AdvancedVKParser(_DummyVk())
    _isolate_jaccard(parser)

    a = "В Кильмези в субботу откроется новая детская площадка рядом со школой номер три"
    b = "Администрация района сообщает график отключения горячей воды на следующей неделе"
    r1 = await parser._filter_post(_fresh_post(-1, 1, a), "novost", None, [], set(), set())
    r2 = await parser._filter_post(_fresh_post(-2, 2, b), "novost", None, [], set(), set())
    assert r1 is not None
    assert r2 is not None
    assert parser.stats["near_dup_jaccard"] == 0


@pytest.mark.asyncio
async def test_jaccard_disabled_flag_lets_reworded_pass():
    """При выключенном флаге переписанный дубль проходит (SimHash тоже отключён)."""
    parser = AdvancedVKParser(_DummyVk())
    _isolate_jaccard(parser, enabled=False)

    base = (
        "В Кильмези в субботу откроется новая детская площадка рядом со школой "
        "номер три приходите всей семьёй на большой праздник"
    )
    reworded = (
        "Приходите всей семьёй на большой праздник в субботу в Кильмези рядом "
        "со школой номер три откроется новая детская площадка"
    )
    r1 = await parser._filter_post(_fresh_post(-1, 1, base), "novost", None, [], set(), set())
    r2 = await parser._filter_post(_fresh_post(-2, 2, reworded), "novost", None, [], set(), set())
    assert r1 is not None
    assert r2 is not None
    assert parser.stats["near_dup_jaccard"] == 0
