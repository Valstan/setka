"""Регрессия: тип ``oauth_refresh_tokens.family_id`` в модели = тип в схеме.

Инцидент 2026-07-26 — вторая колонка того же класса, что ``radar_users.sub``
(см. ``test_radar_user_schema.py``). Миграция 052 создала ``family_id UUID``,
модель объявляла ``String(36)``; Postgres отвергал INSERT («column "family_id"
is of type uuid but expression is of type character varying»), и обмен
authorization code на токены падал 500 **на happy path**: authorize, проверка
PKCE и клиентских кредов проходили, ломалась запись refresh-токена. Ветка 401
(неверный секрет) при этом отвечала штатно — поэтому клиент видел здоровый
эндпоинт, который валится только на реальном обмене.

Вскрыто первым живым round-trip'ом клиента ``trener`` (письмо brain
2026-07-26, urgency high). Общий гейт по ВСЕМ миграциям и моделям —
``tests/test_schema_type_parity.py``; здесь — именной тест на конкретный
инцидент, чтобы он был виден в дифе.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import uuid as uuid_module  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: E402

from database.models_extended import OAuthRefreshToken  # noqa: E402


def test_family_id_column_is_postgres_uuid():
    """``family_id`` объявлен как UUID — иначе обмен кода на токены упадёт 500."""
    column = OAuthRefreshToken.__table__.c.family_id
    assert isinstance(column.type, PgUUID), (
        f"oauth_refresh_tokens.family_id объявлен как {column.type!r}, а в схеме "
        "он UUID (database/migrations/052_*.sql) — INSERT refresh-токена упадёт"
    )


def test_family_id_stays_python_string():
    """Питоновская сторона — строка: так family_id чеканится и сравнивается."""
    assert OAuthRefreshToken.__table__.c.family_id.type.as_uuid is False


@pytest.mark.asyncio
async def test_refresh_token_row_round_trips(db_session):
    """Строка refresh-токена реально пишется и читается по строковому family_id.

    Тот самый INSERT, который падал на проде: если тип модели разойдётся со
    схемой снова, здесь это увидит уже вставка, а не только сверка типов.
    """
    from datetime import datetime, timedelta

    from database.models_extended import RadarUser

    user = RadarUser(login="tok-user", role="radar")
    db_session.add(user)
    await db_session.flush()

    family_id = str(uuid_module.uuid4())
    db_session.add(
        OAuthRefreshToken(
            token_hash="a" * 64,
            family_id=family_id,
            user_id=user.id,
            client_id="trener",
            scope="openid profile email",
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
    )
    await db_session.flush()

    found = (
        await db_session.execute(
            select(OAuthRefreshToken).where(OAuthRefreshToken.family_id == family_id)
        )
    ).scalar_one()
    assert found.family_id == family_id
    assert isinstance(found.family_id, str)
