"""Гейт: типы колонок в ORM-моделях не расходятся с типами в SQL-миграциях.

Дважды за два дня одна и та же ошибка ломала прод по-тихому: миграция 052
объявила колонку ``UUID``, модель — ``String``, а Postgres отвергает вставку
строки в uuid-колонку. 25.07 это был ``radar_users.sub`` (падало создание
любого аккаунта), 26.07 — ``oauth_refresh_tokens.family_id`` (падал обмен
authorization code на токены). Оба раза: линтер чист, тесты с мокнутой сессией
зелёные, ошибка вылезла только на живом запросе к Postgres.

Класс ошибок общий: **истина о типе живёт в двух местах** — в `.sql` и в
`Column(...)`, — и ничто их не сверяет. Точечный тест на конкретную колонку
ловит уже случившийся инцидент, но не следующий. Поэтому здесь не список
колонок, а выведенное правило: парсим все миграции, берём каждую колонку, для
которой есть и SQL-объявление, и ORM-колонка, и требуем совпадения по признаку
«UUID / не UUID» в обе стороны (модель отстала от схемы ⇄ схема отстала от
модели).

Сверка идёт по признаку UUID, а не по полному типу: остальные расхождения
(VARCHAR(64) против VARCHAR(128), INTEGER против BIGINT) Postgres прощает
или они безобидны, а uuid↔varchar — жёсткий отказ на INSERT.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import database.models  # noqa: F401,E402 — регистрация таблиц в metadata
import database.models_extended  # noqa: F401,E402
from database.connection import Base  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "database" / "migrations"

# Строки-констрейнты внутри CREATE TABLE — не колонки.
_NOT_A_COLUMN = {
    "constraint",
    "primary",
    "unique",
    "foreign",
    "check",
    "key",
    "exclude",
    "like",
}

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([\w.]+)\"?\s*\(", re.IGNORECASE
)
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?\"?([\w.]+)\"?\s+ADD\s+COLUMN\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s+([A-Za-z]+)",
    re.IGNORECASE,
)
_ALTER_TYPE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?\"?([\w.]+)\"?\s+ALTER\s+COLUMN\s+\"?(\w+)\"?\s+"
    r"(?:SET\s+DATA\s+)?TYPE\s+([A-Za-z]+)",
    re.IGNORECASE,
)
_COLUMN_DEF_RE = re.compile(r"^\s*\"?(\w+)\"?\s+([A-Za-z]+)")


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def _split_top_level_commas(body: str) -> list[str]:
    """Разбить тело CREATE TABLE по запятым верхнего уровня (не внутри скобок)."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    tail = "".join(buf)
    if tail.strip():
        parts.append(tail)
    return parts


def _create_table_bodies(sql: str):
    """(table, body) для каждого CREATE TABLE — по балансу скобок, не по regex."""
    for match in _CREATE_TABLE_RE.finditer(sql):
        table = match.group(1).split(".")[-1].lower()
        start = match.end()  # сразу за открывающей скобкой
        depth = 1
        pos = start
        while pos < len(sql) and depth:
            if sql[pos] == "(":
                depth += 1
            elif sql[pos] == ")":
                depth -= 1
            pos += 1
        yield table, sql[start : pos - 1]


def collect_sql_column_types() -> dict[tuple[str, str], str]:
    """{(таблица, колонка): SQL-тип} по всем миграциям.

    Последнее объявление побеждает: файлы читаются по возрастанию номера, так
    что ``ALTER COLUMN ... TYPE`` в поздней миграции перекрывает исходный тип.
    """
    types: dict[tuple[str, str], str] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = _strip_sql_comments(path.read_text(encoding="utf-8"))

        for table, body in _create_table_bodies(sql):
            for chunk in _split_top_level_commas(body):
                match = _COLUMN_DEF_RE.match(chunk)
                if not match:
                    continue
                column, sql_type = match.group(1).lower(), match.group(2).upper()
                if column in _NOT_A_COLUMN:
                    continue
                types[(table, column)] = sql_type

        for regex in (_ADD_COLUMN_RE, _ALTER_TYPE_RE):
            for match in regex.finditer(sql):
                table = match.group(1).split(".")[-1].lower()
                types[(table, match.group(2).lower())] = match.group(3).upper()
    return types


def _orm_is_uuid(column) -> bool:
    """UUID ли колонка с точки зрения SQLAlchemy (postgresql.UUID или generic)."""
    from sqlalchemy.dialects.postgresql import UUID as PgUUID

    uuid_types: tuple[type, ...] = (PgUUID,)
    generic = getattr(__import__("sqlalchemy"), "Uuid", None)
    if generic is not None:
        uuid_types += (generic,)
    return isinstance(column.type, uuid_types)


def test_migrations_are_parseable():
    """Парсер что-то находит и видит обе известные uuid-колонки.

    Без этого теста гейт может молча деградировать до нуля проверок (сломался
    regex, переехала папка) и при этом оставаться зелёным.
    """
    sql_types = collect_sql_column_types()
    assert len(sql_types) > 100, f"распознано подозрительно мало колонок: {len(sql_types)}"
    assert sql_types.get(("radar_users", "sub")) == "UUID"
    assert sql_types.get(("oauth_refresh_tokens", "family_id")) == "UUID"


def test_uuid_columns_match_between_models_and_migrations():
    """Ни одна колонка не объявлена uuid в одном месте и varchar в другом."""
    sql_types = collect_sql_column_types()
    mismatches: list[str] = []

    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            sql_type = sql_types.get((table_name.lower(), column.name.lower()))
            if sql_type is None:
                continue  # колонки нет в миграциях — сверять не с чем
            sql_is_uuid = sql_type == "UUID"
            orm_is_uuid = _orm_is_uuid(column)
            if sql_is_uuid and not orm_is_uuid:
                mismatches.append(
                    f"{table_name}.{column.name}: в схеме UUID, в модели {column.type!r} — "
                    "Postgres отвергнет INSERT (модель отстала от миграции)"
                )
            elif orm_is_uuid and not sql_is_uuid:
                mismatches.append(
                    f"{table_name}.{column.name}: в модели UUID, в схеме {sql_type} — "
                    "нужна миграция ALTER COLUMN TYPE UUID (схема отстала от модели)"
                )

    assert not mismatches, "Расхождение типов модель↔схема:\n" + "\n".join(mismatches)
