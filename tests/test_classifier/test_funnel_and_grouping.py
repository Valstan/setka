"""Воронка фильтра, прогресс оператора и аранжировка ленты (заказ владельца 2026-08-19).

Три вопроса, на которые панель отвечать не умела: доходит ли собранное до движка,
насколько оператор отстаёт от движка и как разбирать ленту пачками, а не поштучно.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from database.models_extended import (
    BulletinCurationRun,
    ClassificationCorrection,
    CollectedPostAudit,
    ContentClassification,
)
from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict


async def _audit(session, *, lip, region="mi", decision="kept", hours_ago=1, text="t"):
    session.add(
        CollectedPostAudit(
            lip=lip,
            region_code=region,
            post_text=text,
            post_url=f"https://vk.com/wall{lip}",
            has_media=False,
            decision=decision,
            collected_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
    )
    await session.commit()


async def _verdict(
    session, *, lip, action="publish", theme="новости", region="mi", text="t", hours_ago=1
):
    await service.record_verdicts(
        session,
        [ClassifierVerdict(lip=lip, theme=theme, action=action, region_code=region, text=text)],
    )
    row = await service.review_feed(session, limit=500)
    del row
    obj = (
        await session.execute(
            ContentClassification.__table__.select().where(ContentClassification.lip == lip)
        )
    ).first()
    # created_at выставляем вручную: окно воронки считается по нему
    await session.execute(
        ContentClassification.__table__.update()
        .where(ContentClassification.lip == lip)
        .values(created_at=datetime.utcnow() - timedelta(hours=hours_ago))
    )
    await session.commit()
    return obj


# ───────────────────────── A. Воронка ─────────────────────────


@pytest.mark.asyncio
async def test_funnel_splits_verdicts_by_action(db_session):
    """Воронка различает, что движок допустил, что выкинул и что отложил."""
    for lip in ("1_1", "1_2", "1_3", "1_4"):
        await _audit(db_session, lip=lip)
    await _verdict(db_session, lip="1_1", action="publish")
    await _verdict(db_session, lip="1_2", action="publish")
    await _verdict(db_session, lip="1_3", action="delete")
    await _verdict(db_session, lip="1_4", action="hold")

    out = await service.funnel_stats(db_session, hours=24)

    assert out["collected"] == 4
    assert out["classified"] == 4
    assert out["publish"] == 2
    assert out["delete"] == 1
    assert out["hold"] == 1


@pytest.mark.asyncio
async def test_funnel_counts_collected_without_verdict(db_session):
    """Собранное без вердикта видно как разрыв между собрано и размечено."""
    await _audit(db_session, lip="2_1")
    await _audit(db_session, lip="2_2")
    await _verdict(db_session, lip="2_1", action="publish")

    out = await service.funnel_stats(db_session, hours=24)

    assert out["collected"] == 2
    assert out["classified"] == 1
    assert out["unclassified"] == 1


@pytest.mark.asyncio
async def test_funnel_published_requires_a_released_bulletin(db_session):
    """В публикацию засчитывается только пост из СВОД­КИ, которая вышла.

    Кандидат неопубликованной свод­ки в публикацию не попал — иначе цифра
    «дошло до читателя» врала бы ровно на невышедших свод­ках.
    """
    await _audit(db_session, lip="3_1")
    await _audit(db_session, lip="3_2")
    await _verdict(db_session, lip="3_1", action="publish")
    await _verdict(db_session, lip="3_2", action="publish")
    db_session.add(
        BulletinCurationRun(
            region_code="mi",
            theme="novost",
            candidates=[{"lip": "3_1"}],
            total_count=1,
            published_post_id=777,
        )
    )
    db_session.add(
        BulletinCurationRun(
            region_code="mi",
            theme="novost",
            candidates=[{"lip": "3_2"}],
            total_count=1,
            published_post_id=None,
        )
    )
    await db_session.commit()

    out = await service.funnel_stats(db_session, hours=24)

    assert out["published"] == 1


@pytest.mark.asyncio
async def test_funnel_window_excludes_older(db_session):
    """За пределами окна ничего не считается — панель про сутки, а не про месяц."""
    await _audit(db_session, lip="4_1", hours_ago=100)
    await _verdict(db_session, lip="4_1", action="publish", hours_ago=100)

    out = await service.funnel_stats(db_session, hours=24)

    assert out["collected"] == 0
    assert out["classified"] == 0


# ───────────────────── B. Прогресс оператора ─────────────────────


@pytest.mark.asyncio
async def test_progress_rate_counts_operator_clicks_not_reviewed_at(db_session):
    """Темп оператора считается по нажатиям, а не по ``reviewed_at``.

    Регресс на архивацию завала 2026-08-18: 44 177 постам ``reviewed_at``
    проставили одним ``UPDATE``. По нему темп вышел бы фантастическим, а
    отставание — нулевым, хотя оператор не нажал ни разу.
    """
    await _audit(db_session, lip="5_1")
    await _verdict(db_session, lip="5_1", action="publish")
    # массовая архивация: reviewed_at есть, нажатий нет
    await db_session.execute(
        ContentClassification.__table__.update().values(reviewed_at=datetime.utcnow())
    )
    await db_session.commit()

    out = await service.operator_progress_stats(db_session, hours=24)

    assert out["operator_reviewed"] == 0


@pytest.mark.asyncio
async def test_progress_reports_engine_output_and_remaining(db_session):
    """Сколько движок вынес, сколько оператор разобрал и сколько осталось."""
    for lip in ("6_1", "6_2", "6_3"):
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="publish")
    first = (await service.review_feed(db_session, limit=10, only_unreviewed=True))[0]
    await service.agree_all(db_session, first["id"])
    await service.finalize(db_session, first["id"])
    await db_session.commit()

    out = await service.operator_progress_stats(db_session, hours=24)

    assert out["engine_verdicts"] == 3
    assert out["operator_reviewed"] >= 1
    assert out["remaining"] == 2


@pytest.mark.asyncio
async def test_progress_rate_is_none_without_clicks(db_session):
    """Без нажатий темп не выдумывается — ``None``, а не ноль или деление на ноль."""
    await _audit(db_session, lip="7_1")
    await _verdict(db_session, lip="7_1", action="publish")

    out = await service.operator_progress_stats(db_session, hours=24)

    assert out["operator_rate_per_hour"] is None
    assert out["lag_hours"] is None


# ────────────────────── C. Аранжировка ленты ──────────────────────


@pytest.mark.asyncio
async def test_feed_groups_by_action_and_theme(db_session):
    """Лента делится на блоки «вердикт × тема» — однородное идёт подряд."""
    await _audit(db_session, lip="8_1")
    await _audit(db_session, lip="8_2")
    await _audit(db_session, lip="8_3")
    await _verdict(db_session, lip="8_1", action="delete", theme="мусор", text="a")
    await _verdict(db_session, lip="8_2", action="delete", theme="мусор", text="b")
    await _verdict(db_session, lip="8_3", action="publish", theme="новости", text="c")

    blocks = await service.review_feed_grouped(db_session, limit=100)

    keys = {(b["action"], b["theme"]): b["total"] for b in blocks}
    assert keys == {("delete", "мусор"): 2, ("publish", "новости"): 1}


@pytest.mark.asyncio
async def test_feed_collapses_exact_duplicates_into_one_card(db_session):
    """Дословные дубли — одна карточка на группу, остальные id при ней."""
    for lip in ("9_1", "9_2", "9_3"):
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="delete", theme="мусор", text="Один  и  ТОТ же")

    blocks = await service.review_feed_grouped(db_session, limit=100)

    assert len(blocks) == 1
    cards = blocks[0]["cards"]
    assert len(cards) == 1
    assert cards[0]["duplicate_count"] == 3
    assert len(cards[0]["duplicate_ids"]) == 3


@pytest.mark.asyncio
async def test_feed_does_not_group_posts_without_text(db_session):
    """Пустой текст не склеивает разные посты в одну фиктивную группу."""
    for lip in ("10_1", "10_2"):
        await _audit(db_session, lip=lip, text="")
        await _verdict(db_session, lip=lip, action="delete", theme="мусор", text="")

    blocks = await service.review_feed_grouped(db_session, limit=100)

    cards = blocks[0]["cards"]
    assert len(cards) == 2
    assert all(c["duplicate_count"] == 1 for c in cards)


@pytest.mark.asyncio
async def test_feed_grouped_skips_reviewed(db_session):
    """Разобранное в блоки не попадает."""
    await _audit(db_session, lip="11_1")
    await _verdict(db_session, lip="11_1", action="delete", theme="мусор")
    item = (await service.review_feed(db_session, limit=10))[0]
    await service.agree_all(db_session, item["id"])
    await service.finalize(db_session, item["id"])
    await db_session.commit()

    blocks = await service.review_feed_grouped(db_session, limit=100)

    assert blocks == []


# ───────────────────── D. Групповое согласие ─────────────────────


@pytest.mark.asyncio
async def test_bulk_agree_finalizes_every_id_given(db_session):
    """Одно нажатие закрывает всю группу."""
    ids = []
    for lip in ("12_1", "12_2", "12_3"):
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="delete", theme="мусор", text=lip)
    for item in await service.review_feed(db_session, limit=10):
        ids.append(item["id"])

    out = await service.bulk_agree(db_session, ids)
    await db_session.commit()

    assert out["finalized"] == 3
    assert await service.review_feed(db_session, limit=10) == []


@pytest.mark.asyncio
async def test_bulk_agree_ignores_unknown_ids(db_session):
    """Чужой или уже удалённый id не роняет пачку и не считается обработанным."""
    await _audit(db_session, lip="13_1")
    await _verdict(db_session, lip="13_1", action="delete", theme="мусор")
    item = (await service.review_feed(db_session, limit=10))[0]

    out = await service.bulk_agree(db_session, [item["id"], 999999])
    await db_session.commit()

    assert out["finalized"] == 1
    assert out["missing"] == 1


@pytest.mark.asyncio
async def test_bulk_agree_records_corrections_for_the_rate(db_session):
    """Групповое согласие остаётся видимым в agree-rate — иначе пачка обучает вхолостую."""
    for lip in ("14_1", "14_2"):
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="delete", theme="мусор", text=lip)
    ids = [i["id"] for i in await service.review_feed(db_session, limit=10)]

    await service.bulk_agree(db_session, ids)
    await db_session.commit()

    rows = (await db_session.execute(ClassificationCorrection.__table__.select())).all()
    assert rows, "групповое согласие обязано оставлять след в corrections"


@pytest.mark.asyncio
async def test_bulk_correct_applies_the_fix_to_every_post_of_the_group(db_session):
    """Правка на карточке-дубле относится ко всей группе.

    Карточка показывает один пост, но представляет всю группу дословных дублей.
    Поправь она только показанный — остальные 38 остались бы с прежним вердиктом
    и, что хуже, невидимыми: в ленте их нет, они схлопнуты в эту же карточку.
    """
    for lip in ("15_1", "15_2", "15_3"):
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="publish", theme="новости", text="один текст")
    ids = [i["id"] for i in await service.review_feed(db_session, limit=10)]

    out = await service.bulk_correct(
        db_session, ids, verdict_type="action", operator_value="delete"
    )
    await db_session.commit()

    assert out["corrected"] == 3
    # Правка живёт в corrections, а не в verdict: корпус обязан хранить и
    # суждение сети, и поправку оператора — на их расхождении учится движок.
    rows = (
        await db_session.execute(
            ClassificationCorrection.__table__.select().where(
                ClassificationCorrection.verdict_type == "action"
            )
        )
    ).all()
    assert len(rows) == 3
    assert all(r.operator_value == "delete" for r in rows)
    # Групповая правка ЗАКРЫВАЕТ карточки, в отличие от одиночной: решение по
    # дословным дублям принято целиком, второе нажатие «Готово» на каждую из
    # 39 штук — ровно та работа, ради устранения которой группировка и делалась.
    assert await service.review_feed(db_session, limit=10) == []


@pytest.mark.asyncio
async def test_bulk_correct_rejects_unknown_verdict_type(db_session):
    """Неизвестный тип вердикта отбивается, а не пишется молча в корпус."""
    await _audit(db_session, lip="16_1")
    await _verdict(db_session, lip="16_1", action="publish")
    item = (await service.review_feed(db_session, limit=10))[0]

    out = await service.bulk_correct(
        db_session, [item["id"]], verdict_type="nonsense", operator_value="x"
    )

    assert out["ok"] is False
    assert out["corrected"] == 0


# ─────────── Правки после проверки на боевых данных 2026-08-19 ───────────


@pytest.mark.asyncio
async def test_funnel_reports_how_wide_the_publication_journal_is(db_session):
    """Воронка сообщает охват журнала публикаций, а не только число.

    На проде журнал курации ведётся по ОДНОМУ району из 29 собираемых, а цифра
    «дошло до читателя» читается как общая по сети. Число без охвата — это
    заниженная цифра с уверенной подписью, то есть ровно тот вид вранья, ради
    которого из воронки выкинут неизмеримый отсев дублями.
    """
    await _audit(db_session, lip="20_1", region="mi")
    await _audit(db_session, lip="20_2", region="vp")
    await _verdict(db_session, lip="20_1", region="mi")
    await _verdict(db_session, lip="20_2", region="vp")
    db_session.add(
        BulletinCurationRun(
            region_code="mi",
            theme="novost",
            candidates=[{"lip": "20_1"}],
            total_count=1,
            published_post_id=1,
        )
    )
    await db_session.commit()

    out = await service.funnel_stats(db_session, hours=24)

    assert out["published"] == 1
    assert out["published_regions"] == 1
    assert out["collected_regions"] == 2


@pytest.mark.asyncio
async def test_funnel_does_not_report_tokens_it_cannot_measure(db_session):
    """Расход токенов из воронки убран: ``tokens_estimate`` никем не заполняется.

    На проде 0 из 2238 вердиктов за сутки несли эту величину, и панель честно
    рисовала «токенов: 0» — цифру, которую человек прочтёт как «движок ничего
    не потратил», хотя он потратил 1.4 млн.
    """
    await _audit(db_session, lip="21_1")
    await _verdict(db_session, lip="21_1")

    out = await service.funnel_stats(db_session, hours=24)

    assert "tokens" not in out


@pytest.mark.asyncio
async def test_block_counts_cover_the_whole_queue_not_the_shown_cards(db_session):
    """Счётчик блока считает ВСЮ очередь, даже когда карточек показано меньше.

    Иначе заголовок «удалить · мусор — 30» врал бы ровно на невлезших постах, а
    кнопка «Согласен со всем блоком» закрывала бы не то, что обещает.
    """
    for i in range(30):
        lip = f"22_{i}"
        await _audit(db_session, lip=lip)
        await _verdict(db_session, lip=lip, action="delete", theme="мусор", text=f"текст {i}")

    blocks = await service.review_feed_grouped(db_session, cards_per_block=5)

    assert len(blocks) == 1
    assert blocks[0]["total"] == 30
    assert len(blocks[0]["cards"]) == 5
    # Групповое действие обязано покрывать всю группу, а не показанную часть.
    assert len(blocks[0]["ids"]) == 30
    assert blocks[0]["hidden_cards"] == 25
