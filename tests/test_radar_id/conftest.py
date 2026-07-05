"""Fixtures для тестов Радар-ID: временный RS256-ключ + in-memory async БД."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов (Region и др.)
from database.connection import Base
from database.models_extended import OAuthAuthCode, OAuthClient, OAuthRefreshToken, RadarUser
from modules.radar_id import keys as keys_mod


@pytest.fixture()
def rsa_key_env(tmp_path):
    """RS256 PEM во временном файле + env + сброс lru_cache."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "radar_id_rs256.pem"
    path.write_bytes(pem)
    keys_mod._load_private_jwk.cache_clear()
    with patch.dict(os.environ, {"RADAR_ID_PRIVATE_KEY_FILE": str(path)}):
        yield str(path)
    keys_mod._load_private_jwk.cache_clear()


@pytest_asyncio.fixture()
async def db_session():
    """Реальная async-сессия поверх in-memory SQLite (только нужные таблицы)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        RadarUser.__table__,
        OAuthClient.__table__,
        OAuthAuthCode.__table__,
        OAuthRefreshToken.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
