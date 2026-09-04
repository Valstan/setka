"""Авто-фиксация публикаций отложки рекламного кабинета (PR-6, замыкание цикла).

Оператор ставит рекламу в VK-«Отложенные» (блок B). VK публикует сам в
назначенное время — но кабинет об этом не узнавал: статус оставался
``scheduled``, публикация и оплата не фиксировались, пока оператор не отметит
вручную. Этот реконсилер раз в полчаса проверяет отложки, чьё время прошло, и
для опубликованных вызывает :func:`record_published`:

  * ``AdScheduledPost.status`` → ``published``;
  * создаёт ``AdPublication`` (факт выхода);
  * если у отложки есть ``client_id`` и ``price`` — создаёт ``AdPayment`` со
    статусом ``awaiting`` (деньги ждём — owner так решил);
  * двигает клиента в воронке в ``published`` (не понижая paid/lost);
  * пишет событие ``published`` (actor='system') в таймлайн.

:func:`record_published` — ЕДИНСТВЕННАЯ точка фиксации выхода: её же зовёт
диспетчер репостов планировщика предложки (``repost_dispatcher``), который
публикует сам и не ждёт этого beat'а. Одна правда об учёте на оба пути.

Идемпотентность: выбираются только ``status='scheduled'`` — после перевода в
``published`` строка повторно не попадёт, дублей не будет. Строки-репосты
(``kind='repost'``) сюда не попадают по построению: у них ``vk_postponed_post_id``
пуст до выхода, а выходом занимается диспетчер.

VK-проверка вынесена в инъектируемый ``is_published(owner_id, post_id)`` →
``True`` (вышел) | ``False`` (ещё в отложке) | ``None`` (неизвестно/удалён),
чтобы чистую логику реконсиляции можно было покрыть тестами без сети.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select

from database.models import AdClient, AdPayment, AdPublication, AdScheduledPost
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)


def _build_default_checker(user_token: str, community_tokens: Dict[int, str]):
    """Сборка VK-проверки статуса поста через ``wall.getById`` (best-effort).

    Кэширует vk_api-хендл на сообщество. ``post_type``: ``post`` → опубликован,
    ``postpone``/``suggest`` → ещё нет, иначе/пусто → неизвестно.
    """
    import vk_api  # локальный импорт — не тянем в тестах

    sessions: Dict[int, Any] = {}

    def is_published(owner_id: int, post_id: int) -> Optional[bool]:  # pragma: no cover - сеть
        cid = abs(int(owner_id))
        token = community_tokens.get(cid) or user_token
        if not token:
            return None
        if cid not in sessions:
            sessions[cid] = vk_api.VkApi(token=token).get_api()
        api = sessions[cid]
        try:
            res = api.wall.getById(posts=f"{owner_id}_{post_id}")
        except Exception as e:
            logger.warning("wall.getById %s_%s failed: %s", owner_id, post_id, e)
            return None
        items = (
            res if isinstance(res, list) else (res.get("items") if isinstance(res, dict) else [])
        )
        if not items:
            return None
        pt = items[0].get("post_type")
        if pt == "post":
            return True
        if pt in ("postpone", "suggest"):
            return False
        return None

    return is_published


async def build_default_checker_from_routing() -> Optional[Callable[[int, int], Optional[bool]]]:
    """Собрать проверку выхода из живых токенов (для диспетчера репостов)."""
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token and not community_tokens:
        return None
    return _build_default_checker(user_token, community_tokens or {})


async def record_published(
    session,
    row: AdScheduledPost,
    *,
    vk_post_id: Optional[int] = None,
    notify: bool = True,
) -> AdPublication:
    """Зафиксировать выход строки отложки — единая правда об учёте.

    ``vk_post_id`` — id вышедшей записи (по умолчанию ``row.vk_postponed_post_id``;
    диспетчер репостов передаёт id только что созданного репоста). Создаёт
    ``AdPublication``, при ``client_id`` и ненулевой ``price`` — ``AdPayment``
    (``awaiting``), продвигает клиента в воронке, пишет таймлайн, шлёт клиенту
    ВК-уведомление. Commit — на вызывающем.
    """
    post_vk_id = int(vk_post_id) if vk_post_id else row.vk_postponed_post_id
    row.status = "published"
    if post_vk_id and row.vk_postponed_post_id != post_vk_id:
        row.vk_postponed_post_id = post_vk_id
    pub = AdPublication(
        client_id=row.client_id,
        community_vk_id=row.community_vk_id,
        vk_post_id=post_vk_id,
        region_id=row.region_id,
        scheduled_post_id=row.id,
        price=row.price,
        status="published",
        expires_at=row.expires_at,  # срок снятия (С2) переносим на публикацию
    )
    session.add(pub)
    await session.flush()

    # Деньги ждём (owner: «ожидание оплаты»), только если есть клиент и цена.
    if row.client_id and row.price:
        session.add(
            AdPayment(
                client_id=row.client_id,
                amount=row.price,
                status="awaiting",
                scheduled_post_id=row.id,
                note="авто: пост опубликован VK",
            )
        )

    if row.client_id:
        client = await session.get(AdClient, row.client_id)
        if client and client.stage in ("detected", "contacted", "scheduled"):
            client.stage = "published"

    kind = getattr(row, "kind", None) or "post"
    log_interaction(
        session,
        kind="published",
        client_id=row.client_id,
        scheduled_post_id=row.id,
        publication_id=pub.id,
        summary=(
            f"Репост вышел (сообщество {row.community_vk_id})"
            if kind == "repost"
            else f"Пост опубликован VK (сообщество {row.community_vk_id})"
        ),
        actor="system",
    )
    if notify and row.client_id and pub.vk_post_id:
        from modules.ad_cabinet.vk_bot import notify as vk_notify

        await vk_notify.notify_client(
            session,
            row.client_id,
            "📣 Ваш пост вышел: " f"https://vk.com/wall{row.community_vk_id}_{pub.vk_post_id}",
        )
    return pub


async def run_reconcile(
    *,
    session_factory: Optional[Callable] = None,
    is_published: Optional[Callable[[int, int], Optional[bool]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Реконсилировать опубликованные VK отложки → фиксация в CRM. Возвращает счётчики."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or datetime.utcnow()

    # Дефолтная VK-проверка собирается лениво (нужны токены) — только если не инжектирована.
    if is_published is None:
        is_published = await build_default_checker_from_routing()
        if is_published is None:
            logger.warning("reconcile: нет VK-токенов, пропуск")
            return {"reconciled": 0, "checked": 0, "skipped": "no_token"}

    reconciled = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AdScheduledPost).where(
                        AdScheduledPost.status == "scheduled",
                        AdScheduledPost.vk_postponed_post_id.isnot(None),
                        AdScheduledPost.publish_date <= now,
                        # Репосты фиксирует диспетчер планировщика предложки.
                        AdScheduledPost.kind != "repost",
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            try:
                state = is_published(int(row.community_vk_id), int(row.vk_postponed_post_id))
            except Exception as e:  # pragma: no cover - защита
                logger.warning("reconcile check failed for post %s: %s", row.id, e)
                state = None
            if state is not True:
                continue
            await record_published(session, row)
            reconciled += 1

        await session.commit()

    logger.info("reconcile: checked=%d, reconciled=%d", len(rows), reconciled)
    return {"reconciled": reconciled, "checked": len(rows)}
