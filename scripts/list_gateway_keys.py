"""Перечень потребителей VK-шлюза — производный от факта, а не от реестра.

Мандат brain 2026-07-26/08-03: у Мозга таблица потребителей в ``access/INDEX.md``
велась вручную и три недели показывала, что у портала вМалмыже консьюмерского
ключа нет, — тогда как ключ ``VMALMYZHE`` лежал в наших ``gateway_keys``
``is_active=true`` с 13 июля. Портал не пробовал открытую дверь, потому что
реестр говорил, что двери нет. Чинить строку бесполезно: разойдётся снова.
Поэтому перечень **вычисляется из БД** и печатается для отправки письмом.

**Значение ключа не печатается никогда и ни в каком режиме** — только имена,
даты, ``is_active`` и статистика использования. Это инвариант, закреплённый
тестом: перечень уходит наружу (в письмо мозгу), в отличие от вывода
``issue_gateway_key.py``, который показывает секрет один раз оператору.
По той же причине не печатается и ``note``: это свободный текст оператора,
в который со временем попадёт что угодно, — а безопасность вывода не должна
зависеть от дисциплины заполнения заметок.

**Почему не HTTP-эндпоинт под экосистемным ключом** (вариант «а» из письма
brain, для него удобнее). ``X-Ecosystem-Key`` один на всех участников, значит
листинг за ним раздал бы перечень потребителей шлюза каждому держателю ключа —
ровно класс [#124](../../brain_matrica/cross-project-ideas/ideas/124-privilege-added-to-an-existing-channel-inherits-its-auth.md):
новое право в существующем аутентифицированном канале молча достаётся всем,
кто уже держит его ключ. CLI на боксе новой сетевой поверхности не создаёт.

Usage (на хосте setka, под env приложения):
    python scripts/list_gateway_keys.py                 # таблица в терминал
    python scripts/list_gateway_keys.py --format md     # markdown — вставить в письмо
    python scripts/list_gateway_keys.py --format json   # машинно
    python scripts/list_gateway_keys.py --active-only   # только is_active

Колонка ``source``:

- ``db`` — строка в ``gateway_keys`` (единый источник, миграция 059);
- ``env`` — имя есть только в ``GATEWAY_KEY_<PROJECT>`` окружения
  (bootstrap-fallback: шлюз такой ключ принимает, но в БД его нет —
  расхождение видно сразу, а не через три недели);
- ``db+env`` — есть в обоих (значения могут различаться: при совпадении имени
  главнее БД, см. ``modules/gateway/keys.py``).

Колонка ``last_used`` считается по ``gateway_requests`` и **не включает**
события выдачи ключа (``endpoint='issue-key'``): выдача — действие оператора,
а не потребителя, и засчитывать её за использование значило бы снова показывать
желаемое вместо факта. Строки этой таблицы чистятся ретеншеном
(``GATEWAY_REQUESTS_RETENTION_DAYS``, дефолт 90 дней), поэтому пустой
``last_used`` читается как «не использовался в пределах окна хранения», а не
как «не использовался никогда» — окно печатается в шапке вывода.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

COLUMNS = ("name", "source", "is_active", "created_at", "rotated_at", "last_used", "calls")

# Действия оператора в usage-логе — не использование ключа потребителем.
_OPERATOR_ENDPOINTS = ("issue-key",)


def _fmt_dt(value: Optional[datetime]) -> str:
    """Дата для человека: ``YYYY-MM-DD HH:MM`` либо ``—``."""
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")


def build_rows(
    db_keys: List[Dict[str, Any]],
    env_names: List[str],
    usage: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Свести три источника в перечень строк (чистая функция — тестируется без БД).

    ``db_keys`` — записи ``gateway_keys`` (без секретов), ``env_names`` — имена
    из ``GATEWAY_KEY_*``, ``usage`` — ``{name: {"last_used": dt, "calls": int}}``.
    Имя, которого нет в БД, но есть в env, попадает в перечень со ``source=env``:
    шлюз его принимает, и умолчать о нём значило бы повторить ошибку реестра.
    """
    env_set = {n.upper() for n in env_names}
    rows: List[Dict[str, Any]] = []
    seen = set()
    for key in db_keys:
        name = key["name"]
        seen.add(name.upper())
        stat = usage.get(name, {})
        rows.append(
            {
                "name": name,
                "source": "db+env" if name.upper() in env_set else "db",
                "is_active": bool(key["is_active"]),
                "created_at": key.get("created_at"),
                "rotated_at": key.get("rotated_at"),
                "last_used": stat.get("last_used"),
                "calls": int(stat.get("calls") or 0),
                "note": key.get("note") or "",
            }
        )
    for name in sorted(env_set - seen):
        stat = usage.get(name, {})
        rows.append(
            {
                "name": name,
                "source": "env",
                "is_active": True,  # env-ключ шлюз принимает, пока не заведён в БД
                "created_at": None,
                "rotated_at": None,
                "last_used": stat.get("last_used"),
                "calls": int(stat.get("calls") or 0),
                "note": "только в env (bootstrap-fallback, в БД записи нет)",
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows


def render(rows: List[Dict[str, Any]], fmt: str, retention_days: int) -> str:
    """Отрисовать перечень. Секретов на входе нет — печатать нечего."""
    if fmt == "json":
        payload = {
            "retention_days": retention_days,
            "keys": [
                {
                    **{k: r[k] for k in ("name", "source", "is_active", "calls")},
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "rotated_at": r["rotated_at"].isoformat() if r["rotated_at"] else None,
                    "last_used": r["last_used"].isoformat() if r["last_used"] else None,
                }
                for r in rows
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    header = (
        f"Потребители VK-шлюза SETKA — {len(rows)} шт. "
        f"(last_used считается по логу за последние {retention_days} дн.)"
    )
    cells = [
        [
            r["name"],
            r["source"],
            "да" if r["is_active"] else "НЕТ",
            _fmt_dt(r["created_at"]),
            _fmt_dt(r["rotated_at"]),
            _fmt_dt(r["last_used"]),
            str(r["calls"]),
        ]
        for r in rows
    ]
    titles = ["name", "source", "active", "created", "rotated", "last_used", "calls"]

    if fmt == "md":
        lines = [header, ""]
        lines.append("| " + " | ".join(titles) + " |")
        lines.append("|" + "---|" * len(titles))
        lines += ["| " + " | ".join(row) + " |" for row in cells]
        return "\n".join(lines)

    widths = [max(len(titles[i]), *(len(row[i]) for row in cells)) if cells else len(titles[i]) for i in range(len(titles))]
    lines = [header, ""]
    lines.append("  ".join(t.ljust(w) for t, w in zip(titles, widths)))
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in cells]
    return "\n".join(lines)


async def _load(active_only: bool) -> tuple[List[Dict[str, Any]], List[str], Dict[str, Dict[str, Any]]]:
    """Прочитать БД и env. Секреты не читаются вовсе — колонка ``secret`` не выбирается."""
    from sqlalchemy import func, select

    from config.gateway import get_gateway_keys as get_env_gateway_keys
    from database import models  # noqa: F401 — конфигурация мапперов
    from database.connection import AsyncSessionLocal
    from database.models import GatewayKey, GatewayRequest

    async with AsyncSessionLocal() as session:
        stmt = select(
            GatewayKey.name,
            GatewayKey.is_active,
            GatewayKey.created_at,
            GatewayKey.rotated_at,
            GatewayKey.note,
        )
        if active_only:
            stmt = stmt.where(GatewayKey.is_active.is_(True))
        db_keys = [
            {
                "name": row.name,
                "is_active": row.is_active,
                "created_at": row.created_at,
                "rotated_at": row.rotated_at,
                "note": row.note,
            }
            for row in (await session.execute(stmt)).all()
        ]
        usage_rows = (
            await session.execute(
                select(
                    GatewayRequest.project,
                    func.max(GatewayRequest.created_at),
                    func.count(GatewayRequest.id),
                )
                .where(GatewayRequest.endpoint.notin_(_OPERATOR_ENDPOINTS))
                .group_by(GatewayRequest.project)
            )
        ).all()
    usage = {row[0]: {"last_used": row[1], "calls": row[2]} for row in usage_rows}
    env_names = [] if active_only else list(get_env_gateway_keys().keys())
    return db_keys, env_names, usage


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перечень потребителей VK-шлюза (без значений ключей)"
    )
    parser.add_argument("--format", choices=("table", "md", "json"), default="table")
    parser.add_argument(
        "--active-only", action="store_true", help="только is_active (env-ключи не добавляются)"
    )
    args = parser.parse_args()

    from config.gateway import get_gateway_requests_retention_days

    db_keys, env_names, usage = await _load(args.active_only)
    rows = build_rows(db_keys, env_names, usage)
    print(render(rows, args.format, get_gateway_requests_retention_days()))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
