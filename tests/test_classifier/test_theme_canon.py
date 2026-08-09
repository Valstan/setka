"""Канон тем применяется НА ЗАПИСЬ, а не только разовой уборкой (миграция 079).

Миграция 070 нормализовала историю и на этом остановилась: словарь читался
только веб-редактором, а ``record_verdicts`` клал тему ровно так, как прислала
модель. За три недели Корпус набрал 1992 вердикта с 29 неканоническими
написаниями — «реклама» в шести вариантах, включая смешанные кириллицу с
латиницей. Тема — ось agree-rate, группировки правил overlay и дистилляции,
поэтому её расщепление обесценивает статистику молча.

Тесты держат обе стороны: известное написание сводится к канону, а неизвестное
**не подгоняется** — молча подогнанный поток не сообщает ничего.
"""

from __future__ import annotations

import pytest

from database.models_extended import ClassifierTheme, ClassifierThemeAlias, ContentClassification
from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict

CANON = ["новости", "объявления", "православие"]


async def _seed_dictionary(session, aliases=(("реклама", "объявления"),)):
    for i, name in enumerate(CANON):
        session.add(ClassifierTheme(name=name, position=i))
    for alias, canon in aliases:
        session.add(ClassifierThemeAlias(alias=alias, canon=canon))
    await session.commit()


def _verdict(lip, theme):
    return ClassifierVerdict(
        lip=lip,
        action="publish",
        theme=theme,
        confidence=80,
        model="test",
        region_code="mi",
        text="текст поста",
        url=f"https://vk.com/wall{lip}",
    )


async def _theme_of(session, lip):
    from sqlalchemy import select

    row = (
        await session.execute(select(ContentClassification).where(ContentClassification.lip == lip))
    ).scalar_one()
    return row.verdict.get("theme")


# ---------- canonicalize_theme (чистая функция) ----------


def test_canon_map_lookup_is_case_insensitive():
    """«Православие» и «православие» — одна тема, а не две строки словаря."""
    canon_map = {"православие": "православие"}
    assert service.canonicalize_theme("Православие", canon_map) == "православие"


def test_unknown_theme_is_returned_unchanged():
    """Неизвестное написание не подгоняется — оно должно остаться видимым."""
    assert service.canonicalize_theme("рекреация", {"новости": "новости"}) == "рекреация"


def test_empty_theme_is_none():
    assert service.canonicalize_theme("   ", {}) is None
    assert service.canonicalize_theme(None, {}) is None


# ---------- нормализация на запись ----------


@pytest.mark.asyncio
async def test_alias_is_canonicalized_on_write(db_session):
    """«реклама» ложится в Корпус как «объявления» — то, ради чего всё это."""
    await _seed_dictionary(db_session)

    await service.record_verdicts(db_session, [_verdict("1_10", "реклама")])

    assert await _theme_of(db_session, "1_10") == "объявления"


@pytest.mark.asyncio
async def test_case_only_difference_is_canonicalized(db_session):
    """Регистр — самое частое расхождение, лечится без отдельного синонима."""
    await _seed_dictionary(db_session)

    await service.record_verdicts(db_session, [_verdict("1_11", "Православие")])

    assert await _theme_of(db_session, "1_11") == "православие"


@pytest.mark.asyncio
async def test_mixed_alphabet_alias_is_canonicalized(db_session):
    """Смешанные алфавиты («рекламa» с латинской a) — реальный случай прода."""
    await _seed_dictionary(db_session, aliases=(("рекламa", "объявления"),))

    await service.record_verdicts(db_session, [_verdict("1_12", "рекламa")])

    assert await _theme_of(db_session, "1_12") == "объявления"


@pytest.mark.asyncio
async def test_unknown_theme_survives_write_unchanged(db_session):
    """Неизвестное написание записывается как есть, а не выбрасывается."""
    await _seed_dictionary(db_session)

    await service.record_verdicts(db_session, [_verdict("1_13", "рекреация")])

    assert await _theme_of(db_session, "1_13") == "рекреация"


@pytest.mark.asyncio
async def test_canon_wins_over_alias(db_session):
    """Синоним, одноимённый теме, не переписывает саму тему."""
    await _seed_dictionary(db_session, aliases=(("новости", "объявления"),))

    await service.record_verdicts(db_session, [_verdict("1_14", "новости")])

    assert await _theme_of(db_session, "1_14") == "новости"


# ---------- редактор синонимов ----------


@pytest.mark.asyncio
async def test_add_alias_requires_existing_canon(db_session):
    """Синоним в несуществующую тему переписал бы поток в никуда."""
    await _seed_dictionary(db_session)

    res = await service.add_alias(db_session, "туризм", "путешествия")

    assert res == {"ok": False, "error": "unknown_canon"}


@pytest.mark.asyncio
async def test_add_alias_rejects_canon_as_alias(db_session):
    """Тему нельзя объявить синонимом — иначе она переписывала бы сама себя."""
    await _seed_dictionary(db_session)

    res = await service.add_alias(db_session, "Новости", "объявления")

    assert res == {"ok": False, "error": "alias_is_canon"}


@pytest.mark.asyncio
async def test_add_alias_lowercases_and_updates(db_session):
    """Синоним хранится в нижнем регистре; повтор обновляет канон, не дублирует."""
    await _seed_dictionary(db_session, aliases=())

    first = await service.add_alias(db_session, "  Реклама  ", "объявления")
    second = await service.add_alias(db_session, "реклама", "новости")

    assert first["alias"] == "реклама" and first["updated"] is False
    assert second["updated"] is True
    assert [a["canon"] for a in await service.aliases_list(db_session)] == ["новости"]


@pytest.mark.asyncio
async def test_aliases_list_flags_dangling_canon(db_session):
    """Удалённая тема-получатель видна оператору, а не молчит."""
    await _seed_dictionary(db_session, aliases=(("реклама", "снесённая тема"),))

    assert (await service.aliases_list(db_session))[0]["canon_known"] is False


@pytest.mark.asyncio
async def test_delete_alias_removes_it(db_session):
    await _seed_dictionary(db_session)

    res = await service.delete_alias(db_session, "Реклама")

    assert res["removed"] == 1
    assert await service.aliases_list(db_session) == []


# ---------- разовая уборка прошлого ----------


@pytest.mark.asyncio
async def test_canonicalize_existing_fixes_recorded_verdicts(db_session):
    """Уже записанные вердикты чинятся, неизвестные — остаются и попадают в отчёт."""
    await _seed_dictionary(db_session)
    # Пишем мимо нормализации, как это делал прод до починки.
    db_session.add_all(
        [
            ContentClassification(
                lip=lip,
                region_code="mi",
                source="test",
                model="test",
                verdict={"action": "publish", "theme": theme},
                confidence=80,
                shadow=True,
                escalated=False,
            )
            for lip, theme in (("2_1", "реклама"), ("2_2", "рекреация"), ("2_3", "новости"))
        ]
    )
    await db_session.commit()

    res = await service.canonicalize_existing(db_session)

    assert res["moved"] == 1
    assert res["unknown"] == {"рекреация": 1}
    assert await _theme_of(db_session, "2_1") == "объявления"
    assert await _theme_of(db_session, "2_2") == "рекреация"


@pytest.mark.asyncio
async def test_canonicalize_existing_is_idempotent(db_session):
    """Повторный прогон ничего не двигает — иначе им нельзя было бы пользоваться."""
    await _seed_dictionary(db_session)
    db_session.add(
        ContentClassification(
            lip="3_1",
            region_code="mi",
            source="test",
            model="test",
            verdict={"action": "publish", "theme": "реклама"},
            confidence=80,
            shadow=True,
            escalated=False,
        )
    )
    await db_session.commit()

    await service.canonicalize_existing(db_session)
    second = await service.canonicalize_existing(db_session)

    assert second["moved"] == 0
