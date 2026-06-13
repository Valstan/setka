"""Авто-снятие рекламных постов по истечении срока (С2 программы ad-CRM).

Срок размещения задаётся при планировании (``AdScheduledPost.expires_at``) и
переносится на публикацию при авто-фиксации (``AdPublication.expires_at``, см.
publish_reconciler). Эта таска раз в сутки снимает вышедшие посты, чей срок
истёк: ``wall.delete`` → ``AdPublication.status='removed'`` + ``removed_at`` +
событие ``removed`` (actor='system') в таймлайн.

Решения владельца 2026-06-13: срок опционален (нет срока → пост висит вечно,
в выборку не попадает), снимаем по сроку **независимо от оплаты** (должники —
отдельный срез С4), уведомление тихое (только запись в таймлайн).

Время: ``expires_at`` — МСК wall-clock naive (как publish_date), поэтому
сравниваем с МСК-now. ``removed_at`` — момент фактического удаления (UTC).

Идемпотентность: выбираются только ``status='published'`` — после перевода в
``removed`` строка повторно не попадёт. VK-удаление вынесено в инъектируемый
``delete_post(owner_id, post_id) -> bool``, чтобы покрыть логику без сети.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select

from database.models import AdPublication
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


def _build_default_deleter(user_token: str, community_tokens: Dict[int, str]):
    """Сборка VK-удаления через ``wall.delete`` (community-token → user-token)."""
    import vk_api  # локальный импорт — не тянем в тестах

    sessions: Dict[str, Any] = {}

    def _api(token: str):
        if token not in sessions:
            sessions[token] = vk_api.VkApi(token=token).get_api()
        return sessions[token]

    def delete_post(owner_id: int, post_id: int) -> bool:  # pragma: no cover - сеть
        cid = abs(int(owner_id))
        tokens = [t for t in (community_tokens.get(cid), user_token) if t]
        if not tokens:
            return False
        for token in tokens:
            try:
                _api(token).wall.delete(owner_id=int(owner_id), post_id=int(post_id))
                return True
            except Exception as e:
                logger.warning("wall.delete %s_%s failed: %s", owner_id, post_id, e)
                continue
        return False

    return delete_post


async def run_expiry(
    *,
    session_factory: Optional[Callable] = None,
    delete_post: Optional[Callable[[int, int], bool]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Снять вышедшие рекламные посты с истёкшим сроком. Возвращает счётчики."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    if now is None:
        now = datetime.now(MSK).replace(tzinfo=None)

    if delete_post is None:
        from modules.vk_token_router import load_vk_routing

        user_token, community_tokens = await load_vk_routing()
        if not user_token and not community_tokens:
            logger.warning("expiry: нет VK-токенов, пропуск")
            return {"removed": 0, "checked": 0, "skipped": "no_token"}
        delete_post = _build_default_deleter(user_token, community_tokens or {})

    removed = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AdPublication).where(
                        AdPublication.status == "published",
                        AdPublication.expires_at.isnot(None),
                        AdPublication.expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for pub in rows:
            if not pub.vk_post_id:
                continue
            try:
                ok = delete_post(int(pub.community_vk_id), int(pub.vk_post_id))
            except Exception as e:  # pragma: no cover - защита
                logger.warning("expiry delete failed for pub %s: %s", pub.id, e)
                ok = False
            if not ok:
                continue

            pub.status = "removed"
            pub.removed_at = datetime.utcnow()
            log_interaction(
                session,
                kind="removed",
                client_id=pub.client_id,
                scheduled_post_id=pub.scheduled_post_id,
                publication_id=pub.id,
                summary=f"Рекламный пост снят по сроку (сообщество {pub.community_vk_id})",
                actor="system",
            )
            removed += 1

        await session.commit()

    logger.info("expiry: checked=%d, removed=%d", len(rows), removed)
    return {"removed": removed, "checked": len(rows)}
