"""Обновление метрик постов в окне 72 часов (звено 5, шаг 1).

Границы отбора проверяются на чистых функциях: «старше 72 часов не трогаем»
и «уже опубликованное нами не трогаем» — это правила владельца, и они должны
падать тестом, а не выясняться на счёте вызовов ВК.

Ниже — те же границы, но уже на ``select_refresh_candidates``/``apply_metrics``
через in-memory БД (фикстура ``db_session`` из ``conftest.py``): чистые функции
проверяют логику отсева, а эти тесты — что она реально применена к запросу и
записи, а не потерялась на стыке с SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from database.models_extended import CollectedPostAudit
from modules.classifier.metrics_refresh import (
    apply_metrics,
    drop_already_published,
    ref_from_post_url,
    select_refresh_candidates,
)


def test_ref_from_post_url_keeps_owner_sign():
    # lip теряет знак owner_id (abs), а wall.getById его требует. Знак
    # восстанавливаем из post_url, где он сохранён.
    assert ref_from_post_url("https://vk.com/wall-196153274_8272") == (-196153274, 8272)


def test_ref_from_broken_url_is_none():
    for bad in ("", None, "https://vk.com/id1", "https://vk.com/wallабв_1"):
        assert ref_from_post_url(bad) is None, f"url={bad!r}"


def test_drop_already_published_removes_ours_only():
    cands = [((-1, 10), "1_10"), ((-2, 20), "2_20"), ((-3, 30), "3_30")]
    out = drop_already_published(cands, {"2_20"})
    assert [lip for _, lip in out] == ["1_10", "3_30"]


def test_drop_already_published_with_empty_set_keeps_everything():
    cands = [((-1, 10), "1_10")]
    assert drop_already_published(cands, set()) == cands


def _row(lip, owner, post_id, *, decision="kept", published_at=None, collected_at=None, **extra):
    """Строка аудита для тестов БД — без метрик, если не передали ``extra``."""
    return CollectedPostAudit(
        lip=lip,
        region_code="mi",
        post_url=f"https://vk.com/wall{owner}_{post_id}",
        decision=decision,
        published_at=published_at,
        collected_at=collected_at or datetime.utcnow(),
        **extra,
    )


@pytest.mark.asyncio
async def test_select_refresh_candidates_uses_collected_at_when_published_at_is_null(db_session):
    """Запасная ветка: без published_at (наследие до миграции 080) решает collected_at.

    ``published_at > cutoff`` на NULL даёт NULL (не True) — без второй ветки
    в OR такие строки потерялись бы из выборки молча, а это ровно 7774
    существующих строки на проде.
    """
    now = datetime.utcnow()
    row = _row("1_10", -1, 10, published_at=None, collected_at=now - timedelta(hours=1))
    db_session.add(row)
    await db_session.commit()

    out = await select_refresh_candidates(db_session, hours=72)
    assert [lip for _, lip in out] == ["1_10"]


@pytest.mark.asyncio
async def test_select_refresh_candidates_skips_posts_older_than_window(db_session):
    """Пост старше 72 часов не попадает в выборку — граница владельца, не оптимизация."""
    now = datetime.utcnow()
    old = _row(
        "1_20",
        -1,
        20,
        published_at=now - timedelta(hours=100),
        collected_at=now - timedelta(hours=100),
    )
    db_session.add(old)
    await db_session.commit()

    out = await select_refresh_candidates(db_session, hours=72)
    assert out == []


@pytest.mark.asyncio
async def test_select_refresh_candidates_includes_both_kept_and_dropped(db_session):
    """Обе стороны аудита обязаны попасть в выборку — иначе D-024 нечем проверить."""
    now = datetime.utcnow()
    kept = _row("1_30", -1, 30, decision="kept", published_at=now - timedelta(hours=1))
    dropped = _row("1_31", -1, 31, decision="dropped", published_at=now - timedelta(hours=1))
    db_session.add_all([kept, dropped])
    await db_session.commit()

    out = await select_refresh_candidates(db_session, hours=72)
    assert {lip for _, lip in out} == {"1_30", "1_31"}


@pytest.mark.asyncio
async def test_apply_metrics_fills_published_at_only_when_null(db_session):
    """published_at перезаписывается только пока его нет — дата поста не меняется."""
    now = datetime.utcnow()
    existing_date = now - timedelta(days=5)
    has_date = _row("2_1", -1, 1, published_at=existing_date)
    no_date = _row("2_2", -1, 2, published_at=None)
    db_session.add_all([has_date, no_date])
    await db_session.commit()

    new_date = now - timedelta(hours=1)
    metrics = {
        (-1, 1): {"views": 10, "likes": 1, "comments": 0, "reposts": 0, "published_at": new_date},
        (-1, 2): {"views": 20, "likes": 2, "comments": 0, "reposts": 0, "published_at": new_date},
    }
    lip_by_ref = {(-1, 1): "2_1", (-1, 2): "2_2"}

    await apply_metrics(db_session, metrics, lip_by_ref)

    rows = {r.lip: r for r in (await db_session.execute(select(CollectedPostAudit))).scalars()}
    assert rows["2_1"].published_at == existing_date  # уже была — не тронута
    assert rows["2_2"].published_at == new_date  # была NULL — заполнена


@pytest.mark.asyncio
async def test_apply_metrics_writes_none_as_none_not_zero(db_session):
    """Поля, которых ВК не прислал (None), остаются None, а не превращаются в 0."""
    row = _row(
        "3_1", -1, 1, published_at=datetime.utcnow(), views=99, likes=1, comments=2, reposts=3
    )
    db_session.add(row)
    await db_session.commit()

    # ВК прислал только likes; views/comments/reposts в ответе отсутствуют.
    metrics = {
        (-1, 1): {
            "views": None,
            "likes": 5,
            "comments": None,
            "reposts": None,
            "published_at": None,
        }
    }
    lip_by_ref = {(-1, 1): "3_1"}

    await apply_metrics(db_session, metrics, lip_by_ref)

    updated = (
        await db_session.execute(select(CollectedPostAudit).where(CollectedPostAudit.lip == "3_1"))
    ).scalar_one()
    assert updated.views is None
    assert updated.likes == 5
    assert updated.comments is None
    assert updated.reposts is None


@pytest.mark.asyncio
async def test_refresh_metrics_reports_failure_when_nothing_updated(db_session, monkeypatch):
    """checked>0, updated==0 (ВК отказал на всех батчах) — явный неуспех, не тихий ok.

    ``fetch_metrics_for_token`` глотает отказы по-батчево и может вернуть
    пустой словарь даже с живым токеном (бан токена посреди прохода, сетевой
    сбой на всех батчах разом). «Проверено много, обновлено ноль» — тот же
    класс отказа, что инцидент 2026-08-19, где таска рапортовала успех,
    ничего не сделав; без этой проверки он повторился бы молча.
    """
    from unittest.mock import AsyncMock, MagicMock

    import modules.classifier.metrics_refresh as metrics_refresh_mod
    import modules.vk_monitor.post_metrics as post_metrics_mod
    import modules.vk_token_router as token_router_mod
    from modules.classifier.metrics_refresh import refresh_metrics

    row = _row("4_1", -1, 1, published_at=datetime.utcnow() - timedelta(hours=1))
    db_session.add(row)
    await db_session.commit()

    # load_published_lips бьёт в work_tables, которой нет в фикстуре db_session
    # (conftest создаёт только таблицы HITL-классификатора) — не часть того,
    # что проверяет этот тест, поэтому просто отдаём «наших публикаций нет».
    monkeypatch.setattr(metrics_refresh_mod, "load_published_lips", AsyncMock(return_value=set()))
    monkeypatch.setattr(token_router_mod, "get_healthy_read_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(post_metrics_mod, "fetch_metrics_for_token", lambda api, refs, **kw: {})
    monkeypatch.setattr("vk_api.VkApi", MagicMock())

    result = await refresh_metrics(db_session, hours=72)
    assert result == {
        "ok": False,
        "error": "no_metrics_fetched",
        "checked": 1,
        "updated": 0,
        "skipped_published": 0,
    }
