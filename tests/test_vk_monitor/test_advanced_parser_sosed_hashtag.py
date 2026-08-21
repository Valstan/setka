"""Тема `sosed` берёт только посты с `#новости` — и этот отсев ТЕПЕРЬ СЧИТАЕТСЯ.

Соседский обмен тянет чужую районную стену целиком, поэтому в него пускают лишь
посты, которые сосед сам пометил `#новости`. Отсев правильный и не меняется.

Тесты стерегут другое — его ВИДИМОСТЬ. До 2026-08-21 этот `return None` был
единственным в цепочке `_filter_post` без инкремента счётчика: молчаливый выход
между двумя считающими соседями (`posts_filtered_no_attachments` перед ним,
дедуп по медиа после). Невидимый отсев нельзя ни measure-before-promote, ни
заметить, когда он начнёт резать лишнее — он просто не существует ни в одном
отчёте.

Почему это поймали только сейчас: разбор готовности к шагу 3 суммировал отсевы
по логам волн и не смог свести воронку. Недостача и была этим фильтром.
"""

import time

import pytest

from modules.vk_monitor.advanced_parser import AdvancedVKParser


class _DummyVk:
    pass


def _fresh_post(owner_id: int, post_id: int, text: str):
    """Пост с фото: тема `sosed` не-новостная, блок 8 требует вложение."""
    return {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "date": int(time.time()),
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
        "photo": [{"id": post_id, "owner_id": owner_id}],
    }


def _bare_parser():
    parser = AdvancedVKParser(_DummyVk())
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    parser._batch_token_sets = []
    parser._blocked_lips = set()
    return parser


@pytest.mark.asyncio
async def test_sosed_without_hashtag_dropped_and_counted():
    """Отсев срабатывает И инкрементит счётчик — раньше он молчал."""
    parser = _bare_parser()
    post = _fresh_post(-100, 901, "Завтра в клубе концерт, приходите всем районом")

    assert await parser._filter_post(post, "sosed", None, [], set(), set()) is None
    assert parser.stats["posts_filtered_sosed_no_hashtag"] == 1


@pytest.mark.asyncio
async def test_sosed_with_hashtag_passes_and_counter_stays_zero():
    """Счётчик не должен считать то, что прошло: иначе он мерит не отсев."""
    parser = _bare_parser()
    post = _fresh_post(-100, 902, "#новости Завтра в клубе концерт, приходите всем районом")

    assert await parser._filter_post(post, "sosed", None, [], set(), set()) is not None
    assert parser.stats["posts_filtered_sosed_no_hashtag"] == 0


@pytest.mark.asyncio
async def test_hashtag_rule_applies_only_to_sosed():
    """В других темах `#новости` не требуется — счётчик не должен ловить чужое."""
    parser = _bare_parser()
    post = _fresh_post(-100, 903, "Завтра в клубе концерт, приходите всем районом")

    assert await parser._filter_post(post, "novost", None, [], set(), set()) is not None
    assert parser.stats["posts_filtered_sosed_no_hashtag"] == 0


@pytest.mark.asyncio
async def test_counter_key_exists_before_any_post():
    """Ключ заведён в инициализации, а не появляется от первого срабатывания.

    Иначе отчёт по волне без единого отсева не содержал бы поля вовсе, и
    «ноль» было бы неотличимо от «счётчика нет» — ровно та неоднозначность,
    из-за которой фильтр и был невидим.
    """
    parser = _bare_parser()
    assert parser.stats["posts_filtered_sosed_no_hashtag"] == 0
