"""Fixtures конвейера ВК → сайты: in-memory async БД + сеятели двух журналов.

Источник конвейера — пересечение ``bulletin_curation_runs`` (что вошло в
ОПУБЛИКОВАННУЮ сводку) и ``collected_post_audit`` (текст + медиа поста), поэтому
почти каждый тест сеет обе стороны — отсюда ``seed_pair``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models_extended import BulletinCurationRun, CollectedPostAudit, ConveyorDelivery

SITE = {
    "key": "vmalmyzhe",
    "title": "вМалмыже.РФ",
    "ingest_url": "https://вмалмыже.рф/api/ingest/posts",
    "key_env": "VMALMYZHE_INGEST_KEY",
    "source_region": "mi",
    "skip_themes": ("reklama", "neighbors"),
    "sections": ("novosti", "afisha", "zhkh"),
}


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        BulletinCurationRun.__table__,
        CollectedPostAudit.__table__,
        ConveyorDelivery.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def seed_run(
    session,
    *,
    lips,
    region_code="mi",
    theme="novost",
    days_ago=0,
    published=True,
):
    """Сводка района. ``published=False`` — черновик, конвейер такие не берёт."""
    run = BulletinCurationRun(
        region_code=region_code,
        theme=theme,
        candidates=[{"lip": lip, "text": "к", "url": f"https://vk.com/wall{lip}"} for lip in lips],
        total_count=len(lips),
        published_post_id=1234 if published else None,
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(run)
    await session.commit()
    return run


async def seed_audit(session, *, lip, region="mi", text="текст поста", media=None):
    row = CollectedPostAudit(
        lip=lip,
        region_code=region,
        post_text=text,
        post_url=f"https://vk.com/wall{lip}",
        has_media=bool(media),
        media=media,
        decision="kept",
    )
    session.add(row)
    await session.commit()
    return row


async def seed_pair(session, *, lip, theme="novost", days_ago=0, published=True, **audit_kw):
    """Обе стороны разом — обычный случай «пост вошёл в опубликованную сводку»."""
    await seed_run(session, lips=[lip], theme=theme, days_ago=days_ago, published=published)
    return await seed_audit(session, lip=lip, **audit_kw)
