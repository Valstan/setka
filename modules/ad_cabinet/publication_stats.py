"""Сбор метрик рекламных публикаций (С3 программы ad-CRM).

Для вышедших рекламных постов (``ad_publications``) тянем просмотры/лайки/репосты
через ``wall.getById`` (переиспользуем стат-стек modules/vk_monitor: тот же VK-метод
с полем ``views.count``) и пишем снимок в ``views/likes/reposts/stats_updated_at``.

Решение владельца 2026-06-13: метрики = просмотры + лайки + репосты; авто раз в
день (beat) + кнопка ручного обновления (``only_client_id``); показ оператору в CRM
+ отчёт клиенту.

VK-доступ вынесен в инъектируемый ``fetch_stats(refs) -> {(owner, post): {...}}``,
чтобы покрыть логику без сети. По умолчанию собирается из ``load_vk_routing``
(user-token админа видит просмотры; community-token как fallback). never-raises.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select

from database.models import AdPublication

logger = logging.getLogger(__name__)

Ref = Tuple[int, int]  # (owner_id, post_id)


def _build_default_fetcher(user_token: str, community_tokens: Dict[int, str]):
    """Сборка VK-фетчера метрик через ``wall.getById`` (батч до 100)."""
    import vk_api  # локальный импорт — не тянем в тестах

    def fetch_stats(refs: List[Ref]) -> Dict[Ref, Dict[str, int]]:  # pragma: no cover - сеть
        if not refs:
            return {}
        # Группируем по токену: user-token админа (видит просмотры) приоритетнее,
        # иначе community-token сообщества.
        by_token: Dict[str, List[Ref]] = {}
        for owner, pid in refs:
            token = user_token or community_tokens.get(abs(int(owner)))
            if token:
                by_token.setdefault(token, []).append((owner, pid))

        out: Dict[Ref, Dict[str, int]] = {}
        for token, grp in by_token.items():
            api = vk_api.VkApi(token=token).get_api()
            for i in range(0, len(grp), 100):
                chunk = grp[i : i + 100]
                posts_str = ",".join(f"{o}_{p}" for o, p in chunk)
                try:
                    resp = api.wall.getById(posts=posts_str)
                except Exception as e:
                    logger.warning("wall.getById stats batch failed: %s", e)
                    continue
                items = (
                    resp
                    if isinstance(resp, list)
                    else (resp.get("items") if isinstance(resp, dict) else [])
                )
                for it in items or []:
                    try:
                        key = (int(it.get("owner_id")), int(it.get("id")))
                    except (TypeError, ValueError):
                        continue
                    out[key] = {
                        "views": int((it.get("views") or {}).get("count", 0)),
                        "likes": int((it.get("likes") or {}).get("count", 0)),
                        "reposts": int((it.get("reposts") or {}).get("count", 0)),
                    }
        return out

    return fetch_stats


async def run_collect_stats(
    *,
    session_factory: Optional[Callable] = None,
    fetch_stats: Optional[Callable[[List[Ref]], Dict[Ref, Dict[str, int]]]] = None,
    only_client_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Обновить метрики вышедших рекламных публикаций. Возвращает счётчики.

    ``only_client_id`` — обновить только публикации одного клиента (кнопка в
    карточке). Без него — все вышедшие (суточный beat).
    """
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or datetime.utcnow()

    updated = 0
    async with session_factory() as session:
        stmt = select(AdPublication).where(
            AdPublication.status == "published",
            AdPublication.vk_post_id.isnot(None),
        )
        if only_client_id is not None:
            stmt = stmt.where(AdPublication.client_id == only_client_id)
        rows = (await session.execute(stmt)).scalars().all()

        refs: List[Ref] = [(int(p.community_vk_id), int(p.vk_post_id)) for p in rows]
        if not refs:
            return {"updated": 0, "checked": 0}

        if fetch_stats is None:
            from modules.vk_token_router import load_vk_routing

            user_token, community_tokens = await load_vk_routing()
            if not user_token and not community_tokens:
                logger.warning("publication stats: нет VK-токенов, пропуск")
                return {"updated": 0, "checked": len(refs), "skipped": "no_token"}
            fetch_stats = _build_default_fetcher(user_token, community_tokens or {})

        try:
            stats = fetch_stats(refs) or {}
        except Exception as e:  # pragma: no cover - защита
            logger.warning("publication stats fetch failed: %s", e)
            stats = {}

        for pub in rows:
            key = (int(pub.community_vk_id), int(pub.vk_post_id))
            s = stats.get(key)
            if s is None:
                continue
            pub.views = s.get("views", 0)
            pub.likes = s.get("likes", 0)
            pub.reposts = s.get("reposts", 0)
            pub.stats_updated_at = now
            updated += 1

        await session.commit()

    logger.info("publication stats: checked=%d, updated=%d", len(refs), updated)
    return {"updated": updated, "checked": len(refs)}
