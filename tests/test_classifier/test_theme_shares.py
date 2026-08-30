"""Доли наполнения ленты и описания тем (миграция 090, заказ владельца 2026-08-30).

Словарь тем разросся до 21 варианта: 12 канонических плюс девять, которые
движок насочинял мимо словаря — «туризм», «рекреация», «жалоба» и даже «Клуб
Малмыжских Путешественников», то есть название сообщества в роли темы. Причина
одна: границу темы объяснить было негде, в промпт уходил голый список имён.
``description`` закрывает эту дыру, ``share_percent`` даёт владельцу потолок на
долю темы в ленте, ``is_service`` отделяет темы, которым процент бессмысленен.

Тесты держат три вещи: описание доезжает до промпта, тема без описания ничего
не ломает, и доля не теряется молча при слиянии тем.
"""

from __future__ import annotations

import pytest

from database.models_extended import ClassifierTheme
from modules.classifier import rules, service


async def _seed(session, rows):
    """rows: последовательность (name, position, description, share, is_service)."""
    for name, position, description, share, is_service in rows:
        session.add(
            ClassifierTheme(
                name=name,
                position=position,
                description=description,
                share_percent=share,
                is_service=is_service,
            )
        )
    await session.commit()


# ───────── описание уходит в промпт ─────────


@pytest.mark.asyncio
async def test_description_reaches_the_prompt(db_session):
    await _seed(
        db_session,
        [
            ("новости", 1, "Что произошло в районе.", None, False),
            ("кругозор", 2, "Наука и путешествия. НЕ огород и не готовка.", None, False),
        ],
    )
    out = await rules.render_effective_postulates(db_session)
    assert "Выбирай тему СТРОГО из списка: новости, кругозор" in out
    assert "**кругозор** — Наука и путешествия. НЕ огород и не готовка." in out


@pytest.mark.asyncio
async def test_theme_without_description_produces_no_dangling_bullet(db_session):
    # Тема без описания не должна порождать строку «- **имя** — » с пустым хвостом:
    # такая строка учит движок, что у темы описания нет, вместо того чтобы просто
    # промолчать. Пробелы вместо текста считаются отсутствием описания.
    await _seed(
        db_session,
        [
            ("новости", 1, None, None, False),
            ("спорт", 2, "   ", None, False),
        ],
    )
    out = await rules.render_effective_postulates(db_session)
    assert "Выбирай тему СТРОГО из списка: новости, спорт" in out
    assert "**новости** —" not in out
    assert "**спорт** —" not in out


@pytest.mark.asyncio
async def test_prompt_without_dictionary_has_no_theme_block(db_session):
    # Поведение до миграции 090 сохраняется байт-в-байт: пустой словарь → блока нет.
    out = await rules.render_effective_postulates(db_session)
    assert "Разрешённые темы" not in out


# ───────── themes_list отдаёт новые поля ─────────


@pytest.mark.asyncio
async def test_themes_list_exposes_share_description_and_service(db_session):
    await _seed(
        db_session,
        [
            ("новости", 1, "Что произошло.", 50, False),
            ("мусор", 2, "Публиковать нечего.", None, True),
        ],
    )
    out = await service.themes_list(db_session)
    by_name = {row["theme"]: row for row in out}

    assert by_name["новости"]["share_percent"] == 50.0
    # NUMERIC приезжает Decimal, а JSON его не сериализует — отдаём float.
    assert isinstance(by_name["новости"]["share_percent"], float)
    assert by_name["новости"]["description"] == "Что произошло."
    assert by_name["новости"]["is_service"] is False

    assert by_name["мусор"]["share_percent"] is None
    assert by_name["мусор"]["is_service"] is True


# ───────── доля не теряется при слиянии тем ─────────


@pytest.mark.asyncio
async def test_delete_theme_moves_share_into_empty_target(db_session):
    # Посты уезжают под именем получателя. Если доля не переедет вместе с ними,
    # удаление темы молча снимет потолок: контент тот же, ограничения больше нет.
    await _seed(
        db_session,
        [
            ("детский сад", 1, None, 5, False),
            ("дети и образование", 2, None, None, False),
        ],
    )
    res = await service.delete_theme(db_session, "детский сад", "дети и образование")
    assert res["ok"] is True

    rows = await service.themes_list(db_session)
    target = next(r for r in rows if r["theme"] == "дети и образование")
    assert target["share_percent"] == 5.0
    assert all(r["theme"] != "детский сад" for r in rows if r["canon"])


@pytest.mark.asyncio
async def test_delete_theme_keeps_target_own_share(db_session):
    # У получателя доля уже задана — это явный выбор оператора, он и остаётся.
    # Складывать два потолка бессмысленно: потолок не аддитивен.
    await _seed(
        db_session,
        [
            ("детский сад", 1, None, 5, False),
            ("дети и образование", 2, None, 12, False),
        ],
    )
    await service.delete_theme(db_session, "детский сад", "дети и образование")

    rows = await service.themes_list(db_session)
    target = next(r for r in rows if r["theme"] == "дети и образование")
    assert target["share_percent"] == 12.0
