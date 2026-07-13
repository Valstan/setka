"""Нейро-вердикт классификатора выключает пост из сводки (_filter_post шаг 3a)."""

import time

import pytest

from modules.vk_monitor.advanced_parser import AdvancedVKParser


class _DummyVk:
    pass


def _fresh_post(owner_id: int, post_id: int, text: str):
    return {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "date": int(time.time()),
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
    }


def _bare_parser(blocked=None):
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    parser._batch_token_sets = []
    parser._blocked_lips = set(blocked or [])
    return parser


@pytest.mark.asyncio
async def test_blocked_lip_dropped_with_stat():
    parser = _bare_parser(blocked={"100_555"})
    p = _fresh_post(-100, 555, "Пост, который нейронка пометила delete " * 3)
    r = await parser._filter_post(p, "novost", None, [], set(), set())
    assert r is None
    assert parser.stats["posts_filtered_classifier"] == 1


@pytest.mark.asyncio
async def test_unblocked_lip_passes():
    parser = _bare_parser(blocked={"100_999"})
    p = _fresh_post(-100, 555, "Обычная районная новость про ремонт дороги " * 3)
    r = await parser._filter_post(p, "novost", None, [], set(), set())
    assert r is not None
    assert parser.stats["posts_filtered_classifier"] == 0
