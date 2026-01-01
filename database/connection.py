"""
Database connection configuration (async SQLAlchemy)

IMPORTANT:
- No secrets in git. DATABASE_URL must come from environment.
- Pooling is enabled by default for asyncpg; tune via DB_POOL_SIZE/DB_MAX_OVERFLOW.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Store secrets server-side (e.g. /etc/setka/setka.env) "
            "and configure services (systemd) to load it."
        )
    return value


DATABASE_URL = _require_env("DATABASE_URL")


engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQLALCHEMY_ECHO", "0") == "1",
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
)


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


Base = declarative_base()


async def get_db_session():
    """FastAPI dependency for getting async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session_context():
    """Context manager for getting async database session."""
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()


async def init_db():
    """Initialize database tables (development convenience)."""
    async with engine.begin() as conn:
        # Import all models before creating tables
        from database import models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connection."""
    await engine.dispose()

"""
Database connection configuration
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
import os

# Database URL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Store secrets server-side (e.g. /etc/setka/setka.env) and configure services to load it."
    )

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL logging
    pool_pre_ping=True,  # Проверка подключений перед использованием
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
)

# Session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base class for models
Base = declarative_base()


async def get_db_session():
    """
    Dependency for getting async database session
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session_context():
    """
    Context manager for getting async database session
    """
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()


async def init_db():
    """
    Initialize database tables
    """
    async with engine.begin() as conn:
        # Import all models before creating tables
        from database import models  # noqa
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """
    Close database connection
    """
    await engine.dispose()

