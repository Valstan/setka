"""Случайная пачка на проверку (заказ владельца 2026-08-20).

Очередь сверху вниз держала оператора в одном пласте неделями: свежие вердикты
одной темы вытесняли остальные, а завал в других темах никто не видел — на этом
напоролась дистилляция 2026-08-18 («мерили покойника»). Здесь проверяется, что
выдача действительно случайная, действительно по всем районам и действительно
однотемная.
"""

from __future__ import annotations

import pytest

from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict


async def _verdicts(session, specs):
    """specs: [(lip, theme, action, region, text)] → записать вердикты."""
    await service.record_verdicts(
        session,
        [
            ClassifierVerdict(lip=lip, theme=theme, action=action, region_code=region, text=text)
            for lip, theme, action, region, text in specs
        ],
    )
    await session.commit()


@pytest.mark.asyncio
async def test_batch_is_one_theme_across_all_regions(db_session):
    """Пачка однотемная, но районы в ней разные — ради этого всё и делалось."""
    await _verdicts(
        db_session,
        [(f"1_{i}", "происшествия", "publish", f"reg{i}", f"текст {i}") for i in range(12)],
    )
    out = await service.review_feed_random(db_session, limit=10)

    assert out["count"] > 0
    assert len(out["blocks"]) == 1
    block = out["blocks"][0]
    assert block["theme"] == "происшествия"
    regions = {c["region_code"] for c in block["cards"]}
    assert len(regions) > 1, "пачка обязана собираться из разных районов"


@pytest.mark.asyncio
async def test_only_one_theme_gets_into_a_batch(db_session):
    """Две темы в очереди — в выдаче ровно одна из них, не смесь."""
    await _verdicts(
        db_session,
        [(f"2_{i}", "мусор", "delete", "mi", f"мусор {i}") for i in range(10)]
        + [(f"3_{i}", "спорт", "publish", "vp", f"спорт {i}") for i in range(10)],
    )
    out = await service.review_feed_random(db_session, limit=10)
    themes = {(c["verdict"] or {}).get("theme") for c in out["blocks"][0]["cards"]}
    assert themes == {out["theme"]}


@pytest.mark.asyncio
async def test_reviewed_posts_never_come_back(db_session):
    """«Которые я ещё не проверял» — проверенные не возвращаются никогда."""
    await _verdicts(
        db_session, [(f"4_{i}", "новости", "publish", "mi", f"т {i}") for i in range(6)]
    )
    items = await service.review_feed(db_session, limit=100)
    closed = items[0]["id"]
    await service.agree_all(db_session, closed)
    await db_session.commit()

    for _ in range(5):  # выборка случайная — гоняем несколько раз
        out = await service.review_feed_random(db_session, limit=10)
        ids = [i for b in out["blocks"] for i in b["ids"]]
        assert closed not in ids


@pytest.mark.asyncio
async def test_exclude_ids_prevent_walking_in_circles(db_session):
    """«Дать ещё 10» не должна вернуть то, что оператор уже видел и пропустил."""
    await _verdicts(
        db_session, [(f"5_{i}", "культура", "publish", "mi", f"т {i}") for i in range(8)]
    )
    first = await service.review_feed_random(db_session, limit=3)
    seen = [i for b in first["blocks"] for i in b["ids"]]

    second = await service.review_feed_random(db_session, limit=3, exclude_ids=seen)
    again = [i for b in second["blocks"] for i in b["ids"]]
    assert not (set(seen) & set(again))


@pytest.mark.asyncio
async def test_empty_queue_is_reported_not_crashed(db_session):
    """Пустая очередь — штатный ответ, а не исключение на пустом random.choice."""
    out = await service.review_feed_random(db_session, limit=10)
    assert out == {"blocks": [], "count": 0, "theme": None, "themes_available": 0}


@pytest.mark.asyncio
async def test_verbatim_duplicates_collapse_into_one_card(db_session):
    """Десять копий одного текста — одно решение оператора, а не десять."""
    await _verdicts(
        db_session,
        [(f"6_{i}", "объявления", "delete", f"reg{i}", "Продам") for i in range(5)],
    )
    out = await service.review_feed_random(db_session, limit=10)
    block = out["blocks"][0]
    assert len(block["cards"]) == 1
    assert block["cards"][0]["duplicate_count"] == 5
    # ids собираются по ВСЕЙ группе: групповая кнопка обещает закрыть пятерых.
    assert len(block["ids"]) == 5


@pytest.mark.asyncio
async def test_theme_choice_varies_between_calls(db_session):
    """Тема меняется от выдачи к выдаче — иначе «случайная пачка» это одна тема
    навсегда, и редкие темы оператор не увидит никогда."""
    await _verdicts(
        db_session,
        [(f"7_{i}", f"тема{i % 4}", "publish", "mi", f"т {i}") for i in range(40)],
    )
    seen = {(await service.review_feed_random(db_session, limit=5))["theme"] for _ in range(25)}
    assert len(seen) > 1, f"тема не меняется между выдачами: {seen}"


@pytest.mark.asyncio
async def test_block_carries_no_action_because_actions_differ(db_session):
    """Блок собран по теме, а действия внутри разные — заголовок не смеет
    объявлять одно действие на всю пачку."""
    await _verdicts(
        db_session,
        [("8_1", "новости", "publish", "mi", "a"), ("8_2", "новости", "delete", "vp", "b")],
    )
    out = await service.review_feed_random(db_session, limit=10)
    assert out["blocks"][0]["action"] == ""
