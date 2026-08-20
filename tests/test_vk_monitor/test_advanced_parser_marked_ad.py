"""Чужая размеченная реклама режется ДАЖЕ в теме reklama (_filter_post шаг 5c).

Рубрика «объявления» строится из чужих пабликов-агрегаторов, и для неё
рекламный фильтр выключен целиком: ``is_advertisement`` при ``theme='reklama'``
возвращает False первой же строкой. Выключен он ради частных объявлений
(«продам мёд»), но заодно пропускал и **коммерческую рекламу с легальной
маркировкой** — ту, за ре-трансляцию которой ВК уже банил аккаунт админа
(инцидент Уржум 2026-07-08, G151).

Различие, которое тесты и стерегут: `erid:`/`#реклама`/`marked_as_ads` — это
маркеры ЧУЖОГО рекламодателя, частное объявление их не носит никогда. Поэтому
резать по ним безопасно, а по commercial-scoring (цена, руб, купить) — нет: он
выкосил бы саму рубрику.
"""

import time

import pytest

from modules.vk_monitor.advanced_parser import AdvancedVKParser


class _DummyVk:
    pass


def _fresh_post(owner_id: int, post_id: int, text: str, marked_as_ads: bool = False):
    return {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "date": int(time.time()),
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
        "marked_as_ads": marked_as_ads,
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
@pytest.mark.parametrize("theme", ["reklama", "novost"])
async def test_legal_ad_marker_dropped_in_any_theme(theme):
    """`erid:` — маркер чужого рекламодателя, а не частного объявления."""
    parser = _bare_parser()
    post = _fresh_post(-100, 501, "Скидки в новом магазине электроники erid: 2Vfnxwabcde " * 3)
    assert await parser._filter_post(post, theme, None, [], set(), set()) is None
    assert parser.stats["posts_filtered_marked_ad"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", ["reklama", "novost"])
async def test_vk_flag_marked_ad_dropped_in_any_theme(theme):
    """Официальная метка ВК — самый надёжный сигнал, и он игнорировался."""
    parser = _bare_parser()
    post = _fresh_post(-100, 502, "Открылся новый салон связи в центре города " * 3, True)
    assert await parser._filter_post(post, theme, None, [], set(), set()) is None
    assert parser.stats["posts_filtered_marked_ad"] == 1


@pytest.mark.asyncio
async def test_na_pravah_reklamy_dropped_in_reklama():
    """Текст намеренно безобидный.

    Первая версия фикстуры («кредит без справок») уходила в статистику шага 5a
    как скам, и тест был зелёным, ничего не проверив в новом шаге. Порядок
    фильтров при этом верный — скам режется раньше, — но проверять надо тот
    путь, ради которого тест написан.
    """
    parser = _bare_parser()
    post = _fresh_post(-100, 503, "На правах рекламы: открылся цветочный магазин на Ленина " * 3)
    assert await parser._filter_post(post, "reklama", None, [], set(), set()) is None
    assert parser.stats["posts_filtered_marked_ad"] == 1


@pytest.mark.asyncio
async def test_private_ad_survives_in_reklama():
    """Обратная сторона: рубрика не должна обнулиться.

    Частное объявление несёт и цену, и «продам», и телефон — по
    commercial-scoring оно реклама, но маркеров рекламодателя не носит.
    """
    parser = _bare_parser()
    post = _fresh_post(
        -100, 504, "Продам мёд свежий качественный, 500 руб литр, тел 89123456789 " * 3
    )
    assert await parser._filter_post(post, "reklama", None, [], set(), set()) is not None
    assert parser.stats["posts_filtered_marked_ad"] == 0
