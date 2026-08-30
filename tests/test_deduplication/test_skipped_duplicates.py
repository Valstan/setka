"""Первоисточник — тот, кто вышел раньше; дубль запоминается и не возвращается.

Заказ владельца 2026-08-30, дословно: «первичным признаком по дублям должно быть
время публикации, кто раньше — тот первоисточник… отсеянные надо где-то запоминать,
чтобы они больше не залетали… если в опубликованные, то нехорошо, это потом в
статистике отобразится нехорошо».

До этой правки дедуп решал спор порядком просмотра: сообщества тасовались
``random.shuffle``, побеждал случайный, а проигравший исчезал бесследно — и через
пару часов следующая волна брала его как свежий пост. Дубль не устранялся, а
сдвигался во времени.

Тесты держат три вещи:
* спор решает дата публикации, а не порядок на входе;
* отсеянный дубль попадает в журнал и больше не участвует;
* пост, который просто не влез в сводку, в журнал НЕ попадает — у него остаётся
  шанс выйти следующей волной.
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models_extended import SkippedDuplicate
from modules.deduplication import skipped as sk
from modules.vk_monitor.advanced_parser import AdvancedVKParser


class _DummyVk:
    pass


def _post(owner_id: int, post_id: int, text: str, *, age_hours: float = 1.0):
    return {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "date": int(time.time() - age_hours * 3600),
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
        "attachments": [],
    }


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[SkippedDuplicate.__table__])
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ───────── спор решает дата ─────────


@pytest.mark.asyncio
async def test_earlier_post_wins_regardless_of_input_order():
    """Один и тот же текст в двух сообществах: публикуется тот, кто вышел раньше.

    Проверяем ОБА порядка на входе — раньше исход определял именно он.
    """
    text = "В районе открыли новый мост через реку, движение запущено с утра"
    early = _post(-100, 1, text, age_hours=5)
    late = _post(-200, 2, text, age_hours=1)

    for order in ([early, late], [late, early]):
        parser = AdvancedVKParser(_DummyVk())
        out = await parser.filter_posts_list(
            posts=list(order),
            theme="novost",
            region_config=None,
            work_table_lip=[],
            work_table_hash=[],
        )
        assert [p["id"] for p in out] == [1], f"порядок входа изменил исход: {order}"


@pytest.mark.asyncio
async def test_loser_is_remembered_with_the_winner_named():
    text = "В районе открыли новый мост через реку, движение запущено с утра"
    parser = AdvancedVKParser(_DummyVk())
    await parser.filter_posts_list(
        posts=[_post(-200, 2, text, age_hours=1), _post(-100, 1, text, age_hours=5)],
        theme="novost",
        region_config=None,
        work_table_lip=[],
        work_table_hash=[],
    )
    assert len(parser._skipped_duplicates) == 1
    note = parser._skipped_duplicates[0]
    assert note["lip"] == "200_2"
    assert note["original_lip"] == "100_1"
    assert note["reason"] == "text"


@pytest.mark.asyncio
async def test_distinct_posts_are_not_recorded_as_duplicates():
    # Пост, который просто не влез в сводку, дублем не считается: журнал наполняют
    # только те, у кого нашёлся более ранний близнец.
    parser = AdvancedVKParser(_DummyVk())
    out = await parser.filter_posts_list(
        posts=[
            _post(-100, 1, "Открыли новый мост через реку, движение запущено"),
            _post(-200, 2, "Библиотека приглашает на встречу с писателем в субботу"),
        ],
        theme="novost",
        region_config=None,
        work_table_lip=[],
        work_table_hash=[],
    )
    assert len(out) == 2
    assert parser._skipped_duplicates == []


# ───────── журнал ─────────


@pytest.mark.asyncio
async def test_record_and_fetch_roundtrip(db_session):
    added = await sk.record_skipped(
        db_session,
        region_code="mi",
        wave_theme="novost",
        entries=[{"lip": "200_2", "original_lip": "100_1", "reason": "text"}],
    )
    assert added == 1
    assert await sk.fetch_skipped_lips(db_session, "mi") == {"200_2"}


@pytest.mark.asyncio
async def test_second_record_of_the_same_lip_is_ignored(db_session):
    entry = [{"lip": "200_2", "original_lip": "100_1", "reason": "text"}]
    assert (
        await sk.record_skipped(db_session, region_code="mi", wave_theme="novost", entries=entry)
        == 1
    )
    assert (
        await sk.record_skipped(db_session, region_code="mi", wave_theme="novost", entries=entry)
        == 0
    )


@pytest.mark.asyncio
async def test_journal_is_per_region(db_session):
    # Дубль в одном районе не должен глушить ту же новость в соседнем: у каждого
    # района своя лента и свой первоисточник.
    await sk.record_skipped(
        db_session, region_code="mi", wave_theme="novost", entries=[{"lip": "200_2"}]
    )
    assert await sk.fetch_skipped_lips(db_session, "ur") == set()


@pytest.mark.asyncio
async def test_broken_session_returns_empty_not_raises():
    # Журнал не важнее волны: без него дедуп работает как до этой правки.
    assert await sk.fetch_skipped_lips(None, "mi") == set()
    assert (
        await sk.record_skipped(None, region_code="mi", wave_theme="n", entries=[{"lip": "1_1"}])
        == 0
    )


@pytest.mark.asyncio
async def test_prune_removes_only_old_rows(db_session):
    from datetime import datetime, timedelta

    db_session.add(SkippedDuplicate(lip="new_1", region_code="mi"))
    db_session.add(
        SkippedDuplicate(
            lip="old_1",
            region_code="mi",
            detected_at=datetime.utcnow() - timedelta(days=30),
        )
    )
    await db_session.commit()

    assert await sk.prune_skipped(db_session, keep_days=7) == 1
    assert await sk.fetch_skipped_lips(db_session, "mi") == {"new_1"}


# ───────── слияние курсоров ─────────


def test_merge_keeps_order_and_drops_repeats():
    # Парсеру всё равно, откуда приехал lip, но смешивать «опубликовано» и
    # «отсеяно» можно только здесь — чтобы источник каждого был виден в тесте.
    assert sk.merge_dedup_lips(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_merge_handles_empty_sides():
    assert sk.merge_dedup_lips([], ["a"]) == ["a"]
    assert sk.merge_dedup_lips(["a"], []) == ["a"]
    assert sk.merge_dedup_lips(None, None) == []
