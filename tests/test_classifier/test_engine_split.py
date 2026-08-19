"""Метрика качества считается ПО ДВИЖКАМ, а не в среднем по кладбищу.

Разбор завала 2026-08-18 показал, что вердикты облачной рутины (выключена
12.08) и живого DeepSeek расходятся на порядок: ``hold`` 28.9% против 0.4%,
расхождение на дословно одинаковом тексте 34.9% против 4.5%. Урок «делить
завал по ``source`` ДО выводов» был тогда применён к чеканке правил, но не к
панели: сводный agree-rate на 90% состоит из покойника и читается как оценка
работающей системы.

Плюс здоровье: молчание движка обязано быть флагом в ответе API, а не датой,
которую человек вычитает глазами (инцидент 2026-08-19 прожил трое суток).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from database.models_extended import ClassificationCorrection, ContentClassification
from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict


async def _seed_audit(db_session, *, lip, region="mi"):
    from database.models_extended import CollectedPostAudit

    db_session.add(
        CollectedPostAudit(
            lip=lip,
            region_code=region,
            post_text="t",
            post_url=f"https://vk.com/wall{lip}",
            has_media=False,
            decision="kept",
        )
    )
    await db_session.commit()


async def _react(db_session, *, lip, verdict_type, outcome):
    row = (
        await db_session.execute(
            ContentClassification.__table__.select().where(ContentClassification.lip == lip)
        )
    ).first()
    db_session.add(
        ClassificationCorrection(
            classification_id=row.id,
            lip=lip,
            verdict_type=verdict_type,
            outcome=outcome,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_agree_rate_is_split_by_engine(db_session):
    """Оператор согласился с живым движком и не согласился со старой рутиной.

    Сводная цифра даст 50% — и ни одному из движков она не соответствует.
    """
    await _seed_audit(db_session, lip="dead_1")
    await _seed_audit(db_session, lip="live_1")
    await service.record_verdicts(
        db_session,
        [ClassifierVerdict(lip="dead_1", theme="новости", region_code="mi", text="x")],
        source="routine",
    )
    await service.record_verdicts(
        db_session,
        [ClassifierVerdict(lip="live_1", theme="новости", region_code="mi", text="x")],
        source="headless",
    )
    await _react(db_session, lip="dead_1", verdict_type="action", outcome="correct")
    await _react(db_session, lip="live_1", verdict_type="action", outcome="agree")

    stats = await service.agree_rate_stats(db_session)

    assert stats["by_type"]["action"]["agree_rate"] == pytest.approx(0.5)
    assert stats["live_engine"] == "headless"
    assert stats["by_engine"]["headless"]["action"]["agree_rate"] == pytest.approx(1.0)
    assert stats["by_engine"]["routine"]["action"]["agree_rate"] == pytest.approx(0.0)
    assert stats["classified_by_engine"] == {"routine": 1, "headless": 1}


@pytest.mark.asyncio
async def test_engine_split_survives_engine_with_no_corrections(db_session):
    """Движок без единой правки не должен исчезать из счётчика вердиктов."""
    await _seed_audit(db_session, lip="live_2")
    await service.record_verdicts(
        db_session,
        [ClassifierVerdict(lip="live_2", theme="новости", region_code="mi", text="x")],
        source="headless",
    )
    stats = await service.agree_rate_stats(db_session)
    assert stats["classified_by_engine"] == {"headless": 1}
    assert stats["by_engine"] == {}


@pytest.mark.asyncio
async def test_health_flags_silent_engine(db_session):
    """Форма инцидента: вердикт есть, но ему трое суток → ``stale``."""
    await _seed_audit(db_session, lip="old_1")
    await service.record_verdicts(
        db_session,
        [ClassifierVerdict(lip="old_1", theme="новости", region_code="mi", text="x")],
        source="headless",
    )
    stale_at = datetime.utcnow() - timedelta(hours=80)
    await db_session.execute(ContentClassification.__table__.update().values(created_at=stale_at))
    await db_session.commit()

    out = await service.health_stats(db_session)
    assert out["stale"] is True
    assert out["last_verdict_age_hours"] == pytest.approx(80.0, abs=0.2)
    assert out["stale_after_hours"] == service.STALE_AFTER_HOURS


@pytest.mark.asyncio
async def test_health_not_stale_right_after_a_run(db_session):
    await _seed_audit(db_session, lip="new_1")
    await service.record_verdicts(
        db_session,
        [ClassifierVerdict(lip="new_1", theme="новости", region_code="mi", text="x")],
        source="headless",
    )
    out = await service.health_stats(db_session)
    assert out["stale"] is False
    assert out["last_verdict_age_hours"] < 1


@pytest.mark.asyncio
async def test_health_without_any_verdict_is_not_stale(db_session):
    """Нет вердиктов вовсе — не повод кричать: так же молчит и сторож."""
    await _seed_audit(db_session, lip="none_1")
    out = await service.health_stats(db_session)
    assert out["stale"] is False
    assert out["last_verdict_age_hours"] is None
