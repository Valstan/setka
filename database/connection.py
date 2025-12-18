"""
Database connection configuration
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
import os

# Database URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://setka_user:SetkaSecure2025!@localhost:5432/setka"
)

# Create async engine
# Для асинхронного движка используем NullPool или не указываем poolclass
# (по умолчанию будет использован правильный пул для async)
from sqlalchemy.pool import NullPool

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL logging
    poolclass=NullPool,  # NullPool для async движка (или можно убрать poolclass)
    pool_pre_ping=True,  # Проверка подключений перед использованием
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

