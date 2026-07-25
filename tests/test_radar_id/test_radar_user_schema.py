"""Регрессия: тип колонки ``radar_users.sub`` в модели должен совпадать со схемой.

Инцидент 2026-07-25. Миграция 052 создала колонку как ``sub UUID``, а модель
объявляла её ``String(36)``. Postgres отвергает вставку строки в uuid-колонку
(«column "sub" is of type uuid but expression is of type character varying»),
поэтому **создание любого аккаунта молча падало 500** — вход через ВК доходил
до INSERT и валился. Заметили только на первом живом входе, спустя недели после
миграции: в `radar_users` всё это время был ровно один пользователь, заведённый
до 052.

Тест дешёвый и ловит именно дрейф «модель разошлась со схемой» — тот класс
ошибок, который не виден ни линтеру, ни мокнутым тестам сессии.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: E402

from database.models_extended import RadarUser  # noqa: E402


def test_sub_column_is_postgres_uuid():
    """``sub`` объявлен как UUID — иначе INSERT не пройдёт (миграция 052)."""
    column = RadarUser.__table__.c.sub
    assert isinstance(column.type, PgUUID), (
        f"radar_users.sub объявлен как {column.type!r}, а в схеме он UUID "
        "(database/migrations/052_*.sql) — вставка нового аккаунта упадёт"
    )


def test_sub_stays_python_string():
    """Питоновская сторона — строка: так ``sub`` кладётся в JWT и сравнивается."""
    column = RadarUser.__table__.c.sub
    assert column.type.as_uuid is False


def test_sub_default_generates_string_uuid():
    """default обязан отдавать строковый UUID, а не объект ``uuid.UUID``."""
    import uuid as uuid_module

    generated = RadarUser.__table__.c.sub.default.arg(None)
    assert isinstance(generated, str)
    uuid_module.UUID(generated)  # бросит ValueError, если формат не UUID
