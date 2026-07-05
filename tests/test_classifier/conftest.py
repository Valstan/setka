"""Fixtures для тестов HITL-классификатора: in-memory async БД + сид постов."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models import Post, Region
from database.models_extended import ClassificationCorrection, ContentClassification


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        Region.__table__,
        Post.__table__,
        ContentClassification.__table__,
        ClassificationCorrection.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def seed_region(session, *, id_=1, code="mi", name="Малмыж"):
    r = Region(id=id_, code=code, name=name)
    session.add(r)
    await session.commit()
    return r


async def seed_post(session, *, id_, region_id=1, text="пост", status="new", days_ago=0):
    p = Post(
        id=id_,
        region_id=region_id,
        community_id=100,
        vk_post_id=id_,
        vk_owner_id=-200,
        text=text,
        status=status,
        date_published=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(p)
    await session.commit()
    return p
