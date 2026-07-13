"""Self-serve выдача API-ключей VK-шлюза (мандат brain 2026-07-12).

Ключи живут в БД ``gateway_keys`` (единый источник, миграция 059); env
``GATEWAY_KEY_<PROJECT>`` — только bootstrap-fallback. Рестарт web НЕ нужен:
шлюз читает ключи из БД на каждом запросе.

Usage (на хосте setka, под env приложения):
    python scripts/issue_gateway_key.py KAZANSKAYA --note "письмо brain 2026-07-12"
    python scripts/issue_gateway_key.py KAZANSKAYA --rotate     # новый секрет
    python scripts/issue_gateway_key.py KAZANSKAYA --disable    # отключить (env не воскресит)
    python scripts/issue_gateway_key.py KAZANSKAYA --enable     # включить обратно
    python scripts/issue_gateway_key.py --import-env            # перенести env-ключи в БД

Печатает секрет ОДИН раз — передать потребителю по защищённому каналу.
Повторный запуск без --rotate секрет НЕ меняет (идемпотентно).
Каждая выдача/ротация/переключение пишется в usage-лог шлюза
(``gateway_requests``, endpoint ``issue-key``) — видно на /gateway-stats.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import datetime

from sqlalchemy import select

from config.gateway import get_gateway_keys as get_env_gateway_keys
from database import models  # noqa: F401 — конфигурация мапперов
from database.connection import AsyncSessionLocal
from database.models import GatewayKey
from modules.gateway.usage import record_request


async def _log_event(project: str, action: str) -> None:
    """Записать событие выдачи в usage-лог (best-effort, #018)."""
    try:
        await record_request(project, "issue-key", action, None, status=200, ok=True)
    except Exception as e:  # pragma: no cover - defensive
        print(f"warning: usage-log failed: {e}", file=sys.stderr)


async def _upsert_key(
    name: str, rotate: bool, disable: bool, enable: bool, note: str | None
) -> int:
    secret_plain = None
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(GatewayKey).where(GatewayKey.name == name))
        ).scalar_one_or_none()
        if row is None:
            secret_plain = secrets.token_urlsafe(32)
            row = GatewayKey(name=name, secret=secret_plain, is_active=not disable, note=note)
            session.add(row)
            action = "created"
        elif rotate:
            secret_plain = secrets.token_urlsafe(32)
            row.secret = secret_plain
            row.rotated_at = datetime.utcnow()
            action = "rotated"
        elif disable:
            row.is_active = False
            action = "disabled"
        elif enable:
            row.is_active = True
            action = "enabled"
        else:
            action = "unchanged"
        if note is not None:
            row.note = note
        await session.commit()
        active = bool(row.is_active)

    await _log_event(name, action)
    print(f"project: {name}")
    print(f"action: {action}")
    print(f"active: {active}")
    if secret_plain:
        print("secret (показывается ОДИН раз, передать потребителю по защищённому каналу):")
        print(secret_plain)
    elif action == "unchanged":
        print("secret: без изменений (--rotate для перегенерации)")
    return 0


async def _import_env() -> int:
    """Перенести env-ключи ``GATEWAY_KEY_*`` в БД (имена, которых в БД нет).

    Значения берутся как есть (потребители продолжают работать без ре-выдачи).
    После переноса env-строки можно удалять — БД главнее при совпадении имени.
    """
    env_keys = get_env_gateway_keys()
    if not env_keys:
        print("env: ключей GATEWAY_KEY_* не найдено")
        return 0
    imported = []
    async with AsyncSessionLocal() as session:
        existing = {row.name for row in (await session.execute(select(GatewayKey))).scalars().all()}
        for name, secret in sorted(env_keys.items()):
            if name in existing or not secret:
                continue
            session.add(GatewayKey(name=name, secret=secret, note="imported from env"))
            imported.append(name)
        await session.commit()
    for name in imported:
        await _log_event(name, "imported-from-env")
    print(f"imported: {imported or '(ничего — всё уже в БД)'}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Issue/rotate/disable a VK-gateway API key")
    parser.add_argument("project", nargs="?", help="имя проекта (KAZANSKAYA, GONBA...)")
    parser.add_argument("--rotate", action="store_true", help="перегенерировать секрет")
    parser.add_argument("--disable", action="store_true", help="отключить ключ")
    parser.add_argument("--enable", action="store_true", help="включить ключ обратно")
    parser.add_argument("--note", default=None, help="кто/зачем (заявка, письмо brain)")
    parser.add_argument(
        "--import-env", action="store_true", help="перенести env-ключи GATEWAY_KEY_* в БД"
    )
    args = parser.parse_args()

    if args.import_env:
        return await _import_env()
    if not args.project:
        parser.error("нужен PROJECT (или --import-env)")
    if sum([args.rotate, args.disable, args.enable]) > 1:
        parser.error("--rotate/--disable/--enable взаимоисключающие")
    name = args.project.strip().upper()
    return await _upsert_key(name, args.rotate, args.disable, args.enable, args.note)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
