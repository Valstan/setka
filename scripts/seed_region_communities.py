#!/usr/bin/env python3
"""Idempotent seed/upsert of communities into a region pool from a JSON file.

Часть discovery-скила: после ручной нейро-классификации (discover_scan.py +
анализ постов оператором) утверждённые сообщества записываются в пул региона.

JSON-формат (список):
    [{"vk_id": 60609780, "category": "novost",
      "name": "...", "screen_name": "zlo43"}, ...]

``vk_id`` подаётся ПОЛОЖИТЕЛЬНЫМ (как из groups.search) — в БД пишется со
знаком минус (owner_id-форма, как уже хранятся communities). Дедуп по
(region_id, abs(vk_id), category): повторный запуск ничего не дублирует.

Запуск на проде (с окружением, секреты не печатаются)::

    sudo bash -c 'set -a; . /etc/setka/setka.env; set +a; \
      cd /home/valstan/SETKA && \
      ./venv/bin/python /tmp/seed_region_communities.py \
        --region-code kirov_obl --file /tmp/_seed.json'

Сначала прогнать с ``--dry-run``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _confirmed_community_ids(vk_abs_ids: List[int]) -> Optional[set]:
    """Через ``groups.getById`` вернуть подмножество id, подтверждённых VK как
    СООБЩЕСТВА.

    Личные профили пользователей и удалённые/забаненные id VK в ответе
    ``groups.getById`` не возвращает — этим и отличаем сообщество от профиля.
    Закрывает seed-ветку протечки личных профилей в community-пул (brain
    2026-06-30, парный к фиксу ``_harvest_repost_owner_ids`` в discovery).

    Возвращает ``None``, если VK-токен недоступен или вызов упал — тогда
    валидация пропускается, и сидер работает как раньше (важно для окружений
    без VK: локальный dry-run, тесты).
    """
    ids = sorted({int(v) for v in vk_abs_ids if v})
    if not ids:
        return set()
    try:
        from config.runtime import VK_TOKENS
        from modules.vk_monitor.vk_client import VKClient
    except Exception:  # noqa: BLE001 - нет конфигурации VK → валидация недоступна
        return None
    token = next((t for t in (VK_TOKENS or {}).values() if t), None)
    if not token:
        return None
    try:
        client = VKClient(token=token)
        confirmed = {int(g.get("id") or 0) for g in (client.get_groups_by_ids(ids) or [])}
    except Exception as e:  # noqa: BLE001 - VK-ошибка не должна ронять сидер
        print(f"  ! VK-валидация не удалась ({e}); продолжаю без неё", file=sys.stderr)
        return None
    confirmed.discard(0)
    return confirmed


def _split_by_community_confirmation(
    parsed: List[Dict[str, Any]], confirmed: Optional[set]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Разделить распарсенные items на (годные, не-сообщества). Чистая функция.

    ``confirmed`` — множество подтверждённых VK id сообществ, либо ``None`` если
    валидация недоступна (нет токена / VK-ошибка). ``None`` → никого не
    отсеиваем (обратная совместимость). ``set`` → оставляем только items, чей
    ``vk_abs`` подтверждён VK; остальные (личные профили / удалённые) идут во
    вторую группу.
    """
    if confirmed is None:
        return list(parsed), []
    keep: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for p in parsed:
        (keep if p["vk_abs"] in confirmed else dropped).append(p)
    return keep, dropped


async def _run(region_code: str, path: str, dry_run: bool, validate: bool = True) -> int:
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Community, Region

    with open(path, encoding="utf-8") as f:
        items = json.load(f)

    # 1. Parse + normalize (битые записи пропускаем с предупреждением).
    parsed: List[Dict[str, Any]] = []
    for it in items:
        try:
            vk_abs = abs(int(it["vk_id"]))
            category = str(it["category"]).strip()
            name = str(it["name"]).strip()
        except (KeyError, TypeError, ValueError):
            print(f"  ! bad item, skipped: {it}", file=sys.stderr)
            continue
        parsed.append(
            {
                "vk_abs": vk_abs,
                "category": category,
                "name": name,
                "screen_name": it.get("screen_name") or None,
            }
        )

    # 2. Best-effort VK-валидация: отсеять id, которые VK НЕ подтверждает как
    #    сообщество (личные профили / удалённые). Без токена — пропускаем.
    confirmed = _confirmed_community_ids([p["vk_abs"] for p in parsed]) if validate else None
    parsed, not_community = _split_by_community_confirmation(parsed, confirmed)
    if validate and confirmed is None:
        print(
            "  ! VK-валидация пропущена (нет токена) — не проверено, что все "
            "vk_id принадлежат сообществам, а не пользователям",
            file=sys.stderr,
        )
    for p in not_community:
        print(
            f"  ! пропущен не-сообщество (личный профиль / удалён?): "
            f"vk_id={p['vk_abs']} {p['name']!r}",
            file=sys.stderr,
        )

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Region).where(Region.code == region_code))
        region = res.scalars().first()
        if not region:
            print(f"FATAL: region {region_code!r} not found", file=sys.stderr)
            return 2
        rid = region.id

        existing = await session.execute(
            select(Community.vk_id, Community.category).where(Community.region_id == rid)
        )
        seen = {(abs(int(vid)), cat) for vid, cat in existing.all()}

        inserted = Counter()
        skipped = 0
        for p in parsed:
            vk_abs, category = p["vk_abs"], p["category"]
            if (vk_abs, category) in seen:
                skipped += 1
                continue
            seen.add((vk_abs, category))
            inserted[category] += 1
            if not dry_run:
                session.add(
                    Community(
                        region_id=rid,
                        vk_id=-vk_abs,
                        name=p["name"],
                        screen_name=p["screen_name"],
                        category=category,
                        is_active=True,
                    )
                )

        if not dry_run:
            await session.commit()

        total = sum(inserted.values())
        mode = "DRY-RUN (nothing written)" if dry_run else "WRITTEN"
        print(f"[{mode}] region={region_code} (id={rid})")
        for cat, n in sorted(inserted.items()):
            print(f"  +{n:2d}  {cat}")
        print(
            f"  total inserted: {total}; skipped (already present): {skipped}; "
            f"skipped (не сообщество): {len(not_community)}"
        )
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed communities into a region pool")
    ap.add_argument("--region-code", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-validate",
        action="store_true",
        help="не проверять через VK, что каждый vk_id — сообщество (а не личный "
        "профиль). По умолчанию валидация включена (best-effort, нужен VK-токен).",
    )
    args = ap.parse_args()
    return asyncio.run(
        _run(args.region_code, args.file, args.dry_run, validate=not args.no_validate)
    )


if __name__ == "__main__":
    raise SystemExit(main())
