"""Сторож конвенции времени в миграциях: UTC, а не время сервера.

Почему это отдельный файл, а не тест внутри ``test_migrate.py``: тот проверяет
механику применения миграций, а этот — их СОДЕРЖАНИЕ.

Предыстория. На проде ``SHOW timezone`` = ``Host``, поэтому ``now()`` и
``CURRENT_TIMESTAMP`` в SQL отдают московское время, а приложение пишет наивный
UTC (``datetime.utcnow``). Разница ровно три часа, и она молча заложена в
десятки старых миграций. Замер 2026-09-01 на проде: ``vk_tokens`` id=4 имел
``updated_at`` 08:00:17 при UTC-времени 05:04:17 — метка на три часа в будущем.

Старые файлы **сознательно не чиним массово**: дефолты в них не срабатывают
(сырых INSERT в проекте нет, ORM подставляет значение сам), и разовый ALTER по
тридцати колонкам стоит дороже пользы. Живая половина — триггеры — вылечена
миграцией 094. Этот сторож держит границу: НОВЫЕ миграции обязаны писать UTC
явно, иначе список исключений будет расти сам собой и тихо.

Если тест покраснел на вашей новой миграции — не добавляйте её в исключения,
а напишите ``(now() AT TIME ZONE 'utc')``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "database" / "migrations"

#: С этого номера конвенция обязательна. Всё, что раньше, — унаследованный долг,
#: описанный в PENDING_FOLLOWUPS («DEFAULT now() отдаёт MSK»).
FIRST_ENFORCED = 94

#: Голое время сервера: ``now()`` / ``CURRENT_TIMESTAMP`` без ``AT TIME ZONE``.
#:
#: ⚠️ У ``now()`` замыкающего ``\b`` быть НЕ ДОЛЖНО: после ``)`` словесного
#: символа нет, граница слова там не срабатывает, и регулярка молча переставала
#: видеть половину дефекта. Поймано разрешающей проверкой ниже — она для этого и
#: написана.
_NAKED_TIME = re.compile(
    r"(?:\bnow\s*\(\s*\)|\bcurrent_timestamp\b)(?!\s*at\s+time\s+zone)",
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """Убрать ``--``-комментарии: в них живут пояснения и текст отката."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _number(path: Path) -> int | None:
    m = re.match(r"^(\d+)_", path.name)
    return int(m.group(1)) if m else None


def _enforced_migrations() -> list[Path]:
    out = []
    for p in sorted(MIGRATIONS.glob("*.sql")):
        n = _number(p)
        if n is not None and n >= FIRST_ENFORCED:
            out.append(p)
    return out


def test_migrations_directory_is_found():
    """Сторож самого сторожа: пустой список файлов зеленил бы всё молча."""
    assert MIGRATIONS.is_dir(), f"каталог миграций не найден: {MIGRATIONS}"
    assert list(MIGRATIONS.glob("*.sql")), "миграций не найдено — проверка ничего не проверила"


def test_enforced_range_is_not_empty():
    """И ещё один: если под правило не попадает ни один файл, тест бесполезен."""
    assert _enforced_migrations(), (
        f"под конвенцию (номер >= {FIRST_ENFORCED}) не попала ни одна миграция — "
        "проверка выродилась в тавтологию"
    )


@pytest.mark.parametrize("path", _enforced_migrations(), ids=lambda p: p.name)
def test_new_migration_uses_utc_not_server_time(path: Path):
    """В новых миграциях время только явным UTC."""
    body = _strip_sql_comments(path.read_text(encoding="utf-8"))
    hits = _NAKED_TIME.findall(body)
    assert not hits, (
        f"{path.name}: найдено голое время сервера {hits}. На проде timezone=Host, "
        "поэтому now()/CURRENT_TIMESTAMP отдают MSK, а приложение пишет UTC — "
        "разница три часа. Пишите (now() AT TIME ZONE 'utc')."
    )


def test_pattern_catches_the_defect_it_was_written_for():
    """Проверка разрешающая: регулярка обязана краснеть на реальном дефекте.

    Без этого теста сторож мог бы не ловить ничего и оставаться зелёным — ровно
    класс «зелёная галочка, которая ничего не проверила».
    """
    broken = "NEW.updated_at = CURRENT_TIMESTAMP;"
    fixed = "NEW.updated_at = (now() AT TIME ZONE 'utc');"
    assert _NAKED_TIME.search(broken), "регулярка не видит CURRENT_TIMESTAMP"
    assert _NAKED_TIME.search("created_at TIMESTAMP DEFAULT now()"), "регулярка не видит now()"
    assert not _NAKED_TIME.search(fixed), "регулярка ложно срабатывает на правильной форме"
    assert not _NAKED_TIME.search(
        "published_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'UTC')"
    ), "регулярка ложно срабатывает на форме из миграции 092"


# ───────── последнее определение функции побеждает ─────────

_FUNC_DEF = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)\s*\(\s*\)(.*?)\$\$\s*LANGUAGE",
    re.IGNORECASE | re.DOTALL,
)

#: Функции, чьё тело обязано писать UTC. Список не «на всякий случай», а по
#: факту: это все триггерные функции проекта (проверено запросом к pg_trigger на
#: живой базе 2026-09-01 — ровно 7 триггеров и ровно эти две функции).
_TRIGGER_FUNCS = ("update_updated_at_column", "update_vk_tokens_updated_at")


def _last_definition(func: str) -> tuple[str, str] | None:
    """(имя файла, тело) последнего по порядку применения определения функции.

    Порядок применения — лексикографический по имени файла: ровно так работает
    ``discover_migrations`` в ``scripts/migrate.py``.
    """
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        body = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for name, definition in _FUNC_DEF.findall(body):
            if name.lower() == func.lower():
                found = (path.name, definition)
    return found


@pytest.mark.parametrize("func", _TRIGGER_FUNCS)
def test_last_definition_of_trigger_function_writes_utc(func: str):
    """Последнее определение каждой триггерной функции обязано писать UTC.

    Зачем отдельно от проверки «новые миграции чистые». Та смотрит только файлы
    с номера 94, а голый `CURRENT_TIMESTAMP` остаётся в 003/011/021/025/027 —
    и одиночный `psql -f 011_*.sql`, операция, прямо разрешённая
    `database/migrations/README.md`, молча вернул бы дефект на прод. Старые
    файлы трогать нельзя (переписывание применённой миграции — своя болезнь),
    поэтому инвариант формулируется иначе: неважно, сколько раз функция
    переопределена, важно, что ПОСЛЕДНЕЕ определение — правильное.

    До миграции 094 этот тест был бы красным: последним определением
    `update_updated_at_column` было 027 (`CURRENT_TIMESTAMP`), а
    `update_vk_tokens_updated_at` — 003. То есть проверка не тавтологична.
    """
    found = _last_definition(func)
    assert found is not None, f"определение функции {func} не найдено ни в одной миграции"
    filename, body = found
    assert "at time zone" in body.lower(), (
        f"последнее определение {func} — в {filename}, и оно пишет время сервера. "
        "На проде timezone=Host, значит триггер будет затирать UTC приложения "
        "московским временем. Ожидается (now() AT TIME ZONE 'utc')."
    )
    assert not _NAKED_TIME.search(
        body
    ), f"последнее определение {func} ({filename}) содержит голое время сервера"
