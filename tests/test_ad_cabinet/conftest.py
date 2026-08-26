"""Fixtures кабинета рекламодателя: in-memory async БД с реальными таблицами.

Паттерн tests/test_classifier/conftest.py: настоящие запросы вместо
mock-сессий — изоляционные тесты обязаны уметь краснеть на настоящем SQL.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models import (
    AdChatMessage,
    AdClient,
    AdClientPackage,
    AdInteraction,
    AdPayment,
    AdPublication,
    AdScheduledPost,
    Region,
)
from database.models_extended import RadarUser


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        Region.__table__,
        RadarUser.__table__,
        AdClient.__table__,
        AdClientPackage.__table__,
        AdScheduledPost.__table__,
        AdPayment.__table__,
        AdPublication.__table__,
        AdInteraction.__table__,
        AdChatMessage.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
