"""Fixtures для тестов HITL-классификатора: in-memory async БД.

Источник постов — свод­ки (``bulletin_curation_runs.candidates``), не posts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models_extended import (
    BulletinCurationRun,
    ClassificationCorrection,
    CollectedPostAudit,
    ContentClassification,
)


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        BulletinCurationRun.__table__,
        ContentClassification.__table__,
        ClassificationCorrection.__table__,
        CollectedPostAudit.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _cand(lip, text="пост", has_media=False):
    return {"lip": lip, "text": text, "url": f"https://vk.com/wall{lip}", "has_media": has_media}


async def seed_run(session, *, region_code="mi", candidates=None, days_ago=0, theme="novost"):
    """Свод­ка-источник с кандидатами (lips)."""
    run = BulletinCurationRun(
        region_code=region_code,
        theme=theme,
        candidates=candidates or [],
        total_count=len(candidates or []),
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(run)
    await session.commit()
    return run
