"""Размер донорских сообществ района — сырьё для аутрича и для оценки потолка.

Проект знает, сколько подписчиков у собственных ИНФО-групп, но ничего не знает о
тех 1627 местных сообществах, которые парсит. А именно там сидят люди, которых
нужно позвать: жители района уже читают свои группы, просто не знают про нашу
ленту. Без размера этих групп нельзя ни отранжировать кандидатов для ручного
обращения, ни ответить владельцу на вопрос «сколько человек в районе вообще
можно достать».

Цена измерения — четыре вызова: ``groups.getById`` берёт 500 id за раз. Это
дешевле одного прогона одной тематической волны, поэтому обновляем раз в неделю
целиком, без хитростей с инкрементальностью.

Читаем READ-токеном через ``pick_healthy_read_token``: он делает пробу перед
выдачей и не отдаёт мёртвый ключ (инцидент 2026-07-12, когда мёртвый-но-включённый
токен заклинил парсинг на четверо суток).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Community

logger = logging.getLogger(__name__)

# Столько id VK отдаёт за один groups.getById. Совпадает с батчем внутри клиента —
# держим константу здесь только ради читаемости отчёта о числе вызовов.
VK_BATCH = 500


def plan_batches(vk_ids: List[int], batch: int = VK_BATCH) -> List[List[int]]:
    """Разбить id на батчи. Чистая функция — чтобы стоимость прогона проверялась тестом."""
    ids = [abs(int(v)) for v in vk_ids if v]
    unique = sorted(set(ids))
    return [unique[i : i + batch] for i in range(0, len(unique), batch)]


def index_members(items) -> Dict[int, Optional[int]]:
    """``groups.getById``-ответ → ``{abs(vk_id): members_count}``.

    Группа без ``members_count`` (закрытая, забаненная) попадает в словарь со
    значением ``None``: «спрашивали, не ответили» — это не то же самое, что
    «не спрашивали», и в БД должно отличаться от отсутствия строки.
    """
    out: Dict[int, Optional[int]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        gid = item.get("id")
        if gid is None:
            continue
        try:
            out[abs(int(gid))] = item.get("members_count")
        except (TypeError, ValueError):
            continue
    return out


async def refresh_community_members(
    session: AsyncSession,
    *,
    fetch=None,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> Dict:
    """Обновить ``communities.members_count``. Только чтение VK, ничего не публикует.

    Args:
        session: сессия БД.
        fetch: подменяемый загрузчик ``(ids) -> items`` для тестов; по умолчанию
            берётся здоровый READ-токен и штатный VK-клиент.
        now: штамп ``members_checked_at``.
        limit: ограничить число сообществ (обкатка).

    Returns:
        ``{"communities": N, "batches": B, "updated": U, "unknown": X}``.
    """
    now = now or datetime.utcnow()

    query = select(Community.id, Community.vk_id).where(Community.is_active.is_(True))
    if limit:
        query = query.limit(int(limit))
    rows = (await session.execute(query)).all()
    if not rows:
        return {"communities": 0, "batches": 0, "updated": 0, "unknown": 0}

    by_vk: Dict[int, List[int]] = {}
    for community_id, vk_id in rows:
        by_vk.setdefault(abs(int(vk_id)), []).append(community_id)

    batches = plan_batches(list(by_vk.keys()))

    if fetch is None:
        fetch = await _default_fetch(session)
        if fetch is None:
            return {
                "communities": len(rows),
                "batches": 0,
                "updated": 0,
                "unknown": 0,
                "error": "нет здорового READ-токена",
            }

    updated = 0
    unknown = 0
    for chunk in batches:
        items = await fetch(chunk)
        members = index_members(items)
        for vk_id, count in members.items():
            if count is None:
                unknown += 1
            for community_id in by_vk.get(vk_id, []):
                await session.execute(
                    update(Community)
                    .where(Community.id == community_id)
                    .values(members_count=count, members_checked_at=now)
                )
                updated += 1

    await session.commit()
    result = {
        "communities": len(rows),
        "batches": len(batches),
        "updated": updated,
        "unknown": unknown,
    }
    logger.info("promo members refresh: %s", result)
    return result


async def _default_fetch(session: AsyncSession):
    """Загрузчик поверх штатного VK-клиента и здорового READ-токена."""
    from modules.vk_monitor.vk_client import VKClient
    from modules.vk_token_router import pick_healthy_read_token

    candidate = await pick_healthy_read_token(session)
    if not candidate:
        logger.warning("promo members refresh: здорового READ-токена нет, пропускаю")
        return None

    client = VKClient(candidate.token)

    async def _fetch(ids: List[int]):
        return await asyncio.to_thread(client.get_groups_by_ids, ids, "members_count")

    return _fetch
