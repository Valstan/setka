"""Нейро-фильтр ДО первой публикации (заказ владельца 2026-08-19).

Измеренная дыра: волна сама парсит посты и сама же их публикует, а
классификация шла отдельной таской раз в 3 часа по накопленному аудиту.
За 14 дней — ноль вердиктов раньше сбора и 10 743 позже, то есть первая
публикация всегда проходила без нейро-фильтра. Отсев спама при этом —
главная задача движка: за ретрансляцию чужой рекламы ВК банит админа паблика.
"""

from __future__ import annotations

import pytest

from database.models_extended import CollectedPostAudit
from modules.classifier import prepublish, service
from modules.classifier.schema import ClassifierVerdict


@pytest.fixture(autouse=True)
def _enable_gate(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_PREPUBLISH_ENABLED", "1")
    monkeypatch.setattr(prepublish, "_lips_with_verdict", _no_known_verdicts, raising=False)


async def _no_known_verdicts(session, lips, region_code):
    return set()


def _vk_post(owner_id: int, post_id: int, text: str) -> dict:
    return {"owner_id": owner_id, "id": post_id, "text": text}


def test_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_PREPUBLISH_ENABLED", raising=False)
    assert prepublish.prepublish_enabled() is False


def test_lip_matches_the_format_used_by_parser_and_audit():
    """Формат тот же, что у ``lip_of_post``: ``abs(owner_id)_id``, знак теряется.

    Своя формула здесь была бы тихой поломкой — гейт сравнивал бы ключи,
    которых нет в БД, и блокировал бы ноль постов, ничем этого не выдав.
    """
    assert prepublish.post_lip(_vk_post(-123, 456, "t")) == "123_456"
    from utils.post_utils import lip_of_post

    assert prepublish.post_lip(_vk_post(-123, 456, "t")) == lip_of_post(-123, 456)


def test_lip_of_broken_post_is_empty_not_an_exception():
    assert prepublish.post_lip({"text": "нет идентификаторов"}) == ""


def test_items_carry_the_wave_region():
    """Регион в батче — тот, от чьего лица судит модель: гео-правила
    («чужой район → delete») без него дают противоположный вердикт."""
    items = prepublish.to_classifier_items([_vk_post(-1, 2, "новость")], region_code="mi")
    assert items == [
        {
            "lip": "1_2",
            "region_code": "mi",
            "text": "новость",
            # В ссылке знак owner_id нужен, в отличие от lip.
            "url": "https://vk.com/wall-1_2",
            "media": [],
        }
    ]


@pytest.mark.asyncio
async def test_spam_is_blocked_before_publication(db_session, monkeypatch):
    """Главный сценарий: движок сказал delete — пост не должен уйти в печать."""
    posts = [_vk_post(-1, 1, "заработок на дому, пишите в личку"), _vk_post(-1, 2, "открыли мост")]

    monkeypatch.setattr(
        "modules.classifier.rules.render_effective_postulates",
        _fake_postulates,
    )
    monkeypatch.setattr(
        "modules.classifier.headless.classify_posts",
        lambda items, **kw: {
            "verdicts": [
                ClassifierVerdict(lip="1_1", theme="мусор", action="delete", region_code="mi"),
                ClassifierVerdict(lip="1_2", theme="новости", action="publish", region_code="mi"),
            ],
            "failures": [],
            "problems": [],
            "chunks": 1,
            "tokens": 900,
        },
    )
    for lip in ("1_1", "1_2"):
        db_session.add(
            CollectedPostAudit(
                lip=lip,
                region_code="mi",
                post_text="t",
                post_url="u",
                has_media=False,
                decision="kept",
            )
        )
    await db_session.commit()

    blocked = await prepublish.blocked_lips_before_publish(db_session, posts, region_code="mi")
    assert blocked == {"1_1"}

    # Вердикты записаны — оператор увидит их в ленте, а следующие волны
    # переиспользуют, не тратя токены заново.
    stats = await service.agree_rate_stats(db_session)
    assert stats["classified_by_engine"] == {"headless": 2}


@pytest.mark.asyncio
async def test_hold_is_blocked_too(db_session, monkeypatch):
    """`hold` — «нужен человек», а не «можно печатать»: до решения оператора
    пост в сводку не идёт (та же семантика, что у enforce)."""
    monkeypatch.setattr("modules.classifier.rules.render_effective_postulates", _fake_postulates)
    monkeypatch.setattr(
        "modules.classifier.headless.classify_posts",
        lambda items, **kw: {
            "verdicts": [
                ClassifierVerdict(lip="1_3", theme="новости", action="hold", region_code="mi")
            ],
            "failures": [],
            "problems": [],
            "chunks": 1,
            "tokens": 100,
        },
    )
    db_session.add(
        CollectedPostAudit(
            lip="1_3",
            region_code="mi",
            post_text="t",
            post_url="u",
            has_media=False,
            decision="kept",
        )
    )
    await db_session.commit()

    blocked = await prepublish.blocked_lips_before_publish(
        db_session, [_vk_post(-1, 3, "непонятно что")], region_code="mi"
    )
    assert blocked == {"1_3"}


@pytest.mark.asyncio
async def test_engine_failure_is_fail_open_and_loud(db_session, monkeypatch, caplog):
    """Отказ движка не останавливает волну — но и не молчит.

    Fail-closed остановил бы сводки всех районов на любом сбое DeepSeek, а
    инцидент 2026-08-19 показал, что сбой может длиться сутками. Цена
    МОЛЧАЛИВОГО fail-open — те же трое суток спама, поэтому здесь ERROR.
    """
    import logging

    monkeypatch.setattr("modules.classifier.rules.render_effective_postulates", _fake_postulates)
    monkeypatch.setattr(
        "modules.classifier.headless.classify_posts",
        lambda items, **kw: {
            "verdicts": [],
            "failures": ["no_api_key"],
            "problems": [],
            "chunks": 1,
            "tokens": 0,
        },
    )
    with caplog.at_level(logging.ERROR, logger="modules.classifier.prepublish"):
        blocked = await prepublish.blocked_lips_before_publish(
            db_session, [_vk_post(-1, 4, "текст")], region_code="mi"
        )
    assert blocked == set()
    assert "no_api_key" in caplog.text


@pytest.mark.asyncio
async def test_crash_inside_gate_does_not_break_the_wave(db_session, monkeypatch):
    """Классификатор — усилитель, не единая точка отказа."""
    monkeypatch.setattr("modules.classifier.rules.render_effective_postulates", _boom)
    blocked = await prepublish.blocked_lips_before_publish(
        db_session, [_vk_post(-1, 5, "текст")], region_code="mi"
    )
    assert blocked == set()


@pytest.mark.asyncio
async def test_disabled_gate_costs_nothing(db_session, monkeypatch):
    """Выключенный гейт не должен даже смотреть в сторону модели."""
    monkeypatch.setenv("CLASSIFIER_PREPUBLISH_ENABLED", "0")
    monkeypatch.setattr("modules.classifier.headless.classify_posts", _boom_sync)
    blocked = await prepublish.blocked_lips_before_publish(
        db_session, [_vk_post(-1, 6, "текст")], region_code="mi"
    )
    assert blocked == set()


async def _fake_postulates(session):
    return "постулаты"


async def _boom(session):
    raise RuntimeError("рухнуло")


def _boom_sync(*a, **k):  # pragma: no cover — не должен вызываться
    raise AssertionError("модель не должна вызываться при выключенном гейте")


@pytest.mark.asyncio
async def test_gate_reports_the_all_known_outcome(db_session, monkeypatch, caplog):
    """«Всё уже размечено» — законный исход, но он обязан быть слышен.

    Без строки в логе этот путь неотличим от «гейт не отработал вовсе», а
    именно такую немоту мы чинили в самом классификаторе в тот же день.
    """
    import logging

    async def _all_known(session, lips, region_code):
        return set(lips)

    monkeypatch.setattr(prepublish, "_lips_with_verdict", _all_known)
    monkeypatch.setattr("modules.classifier.headless.classify_posts", _boom_sync)

    with caplog.at_level(logging.INFO, logger="modules.classifier.prepublish"):
        blocked = await prepublish.blocked_lips_before_publish(
            db_session, [_vk_post(-1, 7, "текст")], region_code="mi"
        )
    assert blocked == set()
    assert "новых=0" in caplog.text
