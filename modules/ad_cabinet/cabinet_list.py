"""Операторский список кабинетов клиентов (заказ владельца 2026-09-02).

Зачем. Владелец разговаривает с клиентом по телефону или в ВК и должен понять,
**в какой именно кабинет** тот зашёл: аккаунты дублируются (вход через ВК и
через пароль дают две карточки), и «у меня не работает» в одном кабинете
выглядит как «всё нормально» в другом. Поэтому у каждого кабинета — **номер**,
который видят обе стороны: ``ad_clients.id``. Он уникален по построению
(первичный ключ), печатается клиенту в шапке кабинета и владельцу в этом
списке; второго нумератора не заводим — два номера у одного кабинета путали бы
сильнее, чем один.

Что такое кабинет. Карточка ``ad_clients`` с привязанным аккаунтом ЕСА
(``radar_user_id``): без аккаунта в кабинет некому входить, это CRM-клиент из
предложки. По просьбе список расширяется и до таких (``include_unlinked``).

Имя строки — самое человеческое, что есть: имя карточки (при ВК-входе оно
приходит из профиля ВК), затем ``display_name`` аккаунта, затем логин, затем
ВК-id, и только потом «Кабинет №N».

Сортировка — по **последнему движению в кабинете**: сообщение в чате (любой
стороны), действие клиента из журнала (заказ, отмена, вход, ошибка), решение
владельца по его посту (одобрение/отказ), оплата. Кто последним что-то сделал —
тот сверху и постепенно опускается, когда его перебивают другие. Правки
карточки оператором в CRM движением кабинета не считаются: массовая
операторская правка не должна перетасовывать список (тот же довод, что у
``cabinet-activity``).

Считается одним проходом по агрегатам (без N+1): подзапросы по каждой таблице
группируются по ``client_id`` и приклеиваются ``outerjoin``-ом.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func, or_, select

from database.models import (
    AdChatMessage,
    AdClient,
    AdInteraction,
    AdPayment,
    AdPublication,
    AdRequest,
    AdScheduledPost,
)
from database.models_extended import RadarUser

#: Виды журнала, которые считаются движением клиента в кабинете, когда их
#: пишет сам клиент (``actor='client'``). Операторские копии тех же видов
#: (массовая отмена в CRM) движением не считаются.
#: ``cabinet_visit`` сюда сознательно не входит (аудит 2026-09-05): визит пишется
#: раз в час и перебивал чужие реальные заказы в сортировке.
CLIENT_ACTIVITY_KINDS = (
    "cabinet_signup",
    "cabinet_js_error",
    "cabinet_order_refused",
    "client_order",
    "cancelled",
    "linked",
    "payment_claimed",
)

#: Решения владельца по постам кабинета — тоже движение: клиент ждёт их и
#: смотрит, а список должен показать, где что-то только что произошло.
OWNER_DECISION_KINDS = ("moderation_approved", "moderation_rejected", "moderation_failed")

#: Действия владельца от имени клиента (impersonation) — движение в кабинете.
OWNER_ACTION_KINDS = ("owner_cabinet_action",)


def display_name(client: AdClient, user: Optional[RadarUser]) -> str:
    """Самое человеческое имя кабинета из того, что есть."""
    name = (client.name or "").strip()
    if name:
        return name
    if user is not None:
        dn = (user.display_name or "").strip()
        if dn:
            return dn
        login = (user.login or "").strip()
        if login:
            return login
        if user.vk_user_id:
            return f"vk.com/id{int(user.vk_user_id)}"
    if client.author_vk_id:
        return f"vk id {int(client.author_vk_id)}"
    return f"Кабинет №{client.id}"


def _latest_by_client(model, ts_col, *conds):
    """Подзапрос ``client_id → max(ts)`` с необязательными фильтрами."""
    q = select(model.client_id.label("cid"), func.max(ts_col).label("ts"))
    if conds:
        q = q.where(*conds)
    return q.group_by(model.client_id).subquery()


async def list_cabinets(session, *, include_unlinked: bool = False) -> List[Dict[str, Any]]:
    """Строки списка кабинетов, свежие сверху.

    Каждая строка: номер, имя, аккаунт (ВК/логин), последнее движение (когда и
    какое), непрочитанные владельцем сообщения, заказано/опубликовано постов,
    оплачено рублей.
    """
    interactions = _latest_by_client(
        AdInteraction,
        AdInteraction.created_at,
        or_(
            and_(AdInteraction.actor == "client", AdInteraction.kind.in_(CLIENT_ACTIVITY_KINDS)),
            and_(AdInteraction.actor == "owner", AdInteraction.kind.in_(OWNER_ACTION_KINDS)),
            AdInteraction.kind.in_(OWNER_DECISION_KINDS),
        ),
    )
    # Свежесть двигает КЛИЕНТ (аудит 2026-09-05): собственный ответ владельца в
    # чате поднимал его же кабинет наверх как «💬 чат».
    chat_last = _latest_by_client(
        AdChatMessage, AdChatMessage.created_at, AdChatMessage.sender == "client"
    )
    # Факт выхода поста и свежая заявка из предложки/ЛС — тоже движение.
    published_last = _latest_by_client(
        AdPublication, AdPublication.published_at, AdPublication.status == "published"
    )
    requests_last = _latest_by_client(AdRequest, AdRequest.detected_at, AdRequest.status == "new")
    chat_unread = (
        select(AdChatMessage.client_id.label("cid"), func.count().label("n"))
        .where(AdChatMessage.sender == "client", AdChatMessage.read_at.is_(None))
        .group_by(AdChatMessage.client_id)
        .subquery()
    )
    # Время денег — момент ПОДТВЕРЖДЕНИЯ (awaiting→paid), а не paid_at, который
    # реконсилер ставит при создании awaiting (аудит 2026-09-05: оплата не
    # двигала строку). Считаем только status='paid'.
    payments = (
        select(
            AdPayment.client_id.label("cid"),
            func.max(
                case(
                    (
                        AdPayment.status == "paid",
                        func.coalesce(AdPayment.paid_confirmed_at, AdPayment.paid_at),
                    ),
                    else_=None,
                )
            ).label("ts"),
            func.coalesce(
                func.sum(case((AdPayment.status == "paid", AdPayment.amount), else_=0)), 0
            ).label("paid"),
        )
        .group_by(AdPayment.client_id)
        .subquery()
    )
    # «Заказано» — то, что клиент реально ждёт: без отменённых, отклонённых и
    # не дошедших до VK (они уже возвращены в пакет).
    orders = (
        select(AdScheduledPost.client_id.label("cid"), func.count().label("n"))
        .where(AdScheduledPost.status.notin_(("cancelled", "rejected", "failed")))
        .group_by(AdScheduledPost.client_id)
        .subquery()
    )
    published = (
        select(AdPublication.client_id.label("cid"), func.count().label("n"))
        .where(AdPublication.status == "published")
        .group_by(AdPublication.client_id)
        .subquery()
    )

    q = (
        select(
            AdClient,
            RadarUser,
            interactions.c.ts,
            chat_last.c.ts,
            chat_unread.c.n,
            payments.c.ts,
            payments.c.paid,
            orders.c.n,
            published.c.n,
            published_last.c.ts,
            requests_last.c.ts,
        )
        .outerjoin(RadarUser, RadarUser.id == AdClient.radar_user_id)
        .outerjoin(interactions, interactions.c.cid == AdClient.id)
        .outerjoin(chat_last, chat_last.c.cid == AdClient.id)
        .outerjoin(chat_unread, chat_unread.c.cid == AdClient.id)
        .outerjoin(published_last, published_last.c.cid == AdClient.id)
        .outerjoin(requests_last, requests_last.c.cid == AdClient.id)
        .outerjoin(payments, payments.c.cid == AdClient.id)
        .outerjoin(orders, orders.c.cid == AdClient.id)
        .outerjoin(published, published.c.cid == AdClient.id)
    )
    if not include_unlinked:
        # Кабинет = аккаунт ЕСА ИЛИ клиент, который сам что-то делал (ВК-бот заводит
        # карточку без аккаунта — аудит 2026-09-05: такие заказы выпадали из списка).
        client_acted = (
            select(AdInteraction.id)
            .where(AdInteraction.client_id == AdClient.id, AdInteraction.actor == "client")
            .exists()
        )
        q = q.where(or_(AdClient.radar_user_id.isnot(None), client_acted))
    # Архивные карточки (096) в списке кабинетов не показываем.
    q = q.where(AdClient.is_archived.is_(False))

    rows = (await session.execute(q)).all()

    out: List[Dict[str, Any]] = []
    for (
        client,
        user,
        act_ts,
        chat_ts,
        unread,
        pay_ts,
        paid,
        ordered,
        pub_n,
        pub_ts,
        req_ts,
    ) in rows:
        candidates = [
            ("chat", chat_ts),
            ("action", act_ts),
            ("payment", pay_ts),
            ("published", pub_ts),
            ("request", req_ts),
            ("created", client.created_at),
        ]
        kind, ts = max(
            ((k, t) for k, t in candidates if t is not None),
            key=lambda kt: kt[1],
            default=("created", None),
        )
        out.append(
            {
                "id": client.id,
                "name": display_name(client, user),
                "stage": client.stage,
                "trusted": bool(client.trusted),
                "has_account": user is not None,
                "vk_user_id": (
                    int(user.vk_user_id) if user is not None and user.vk_user_id else None
                ),
                "login": user.login if user is not None else None,
                "last_activity_at": ts.isoformat() if isinstance(ts, datetime) else None,
                "last_activity_kind": kind,
                "unread": int(unread or 0),
                "posts_ordered": int(ordered or 0),
                "posts_published": int(pub_n or 0),
                "paid_total": float(paid or 0),
                "region_id": client.region_id,
            }
        )

    # Свежее сверху; без движения — вниз, среди них новее карточка выше.
    out.sort(key=lambda r: (r["last_activity_at"] or "", r["id"]), reverse=True)
    return out


__all__ = ["list_cabinets", "display_name", "CLIENT_ACTIVITY_KINDS"]
