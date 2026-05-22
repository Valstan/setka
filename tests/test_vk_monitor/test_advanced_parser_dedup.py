"""Дедупликация в AdvancedVKParser (lip после unwrap, текст, медиа)."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
