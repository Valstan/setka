"""Вход владельца в кабинет клиента («смотреть его глазами»).

Зачем. Кабинет рекламодателя изолирован жёстко: все хендлеры
``web/api/advertiser_cabinet`` резолвят карточку из СЕССИИ
(``advertiser_link.resolve_client``), а ``client_id`` из запроса не читается
нигде. Инвариант правильный — он и должен таким остаться для клиентов. Но у
владельца из-за него не было способа проверить, что у клиента в кабинете всё
работает: открыв ``/cabinet``, он получал 403 «не рекламодатель», потому что
своей карточки рекламодателя у него нет. Обходной путь — отдельный демо-клиент
``demo_cabinet_probe`` — показывал чужой кабинет ровно настолько, насколько
демо-данные похожи на реальные, то есть не показывал.

Заказ владельца 2026-08-31: «я как суперадмин могу заходить в кабинеты клиентов,
чтобы смотреть, правильно ли всё у них там работает», режим — **полный доступ**
(решение владельца в том же заходе; read-only рассматривался и отклонён).

Устройство — одна дверь, а не шестнадцать. Ключевое решение: ``client_id``
по-прежнему НЕ читается в хендлерах. Читает его только этот модуль, и только
здесь же проверяется, что запрашивающий — владелец. Хендлеры продолжают звать
``_current_client()`` и не знают, что карточка может быть чужой.

Три свойства, без которых фичу нельзя выпускать:

1. **Владельца определяет ровно одна функция** — ``auth_gate.is_owner_account``.
   Вторая копия правила разошлась бы с гейтом молча.
2. **Не-владелец не может ничего** — параметр ``as_client`` у обычного клиента
   не игнорируется, а отвергается явным 403. Молчаливое игнорирование выглядело
   бы как «работает», и первый же баг в вызывающем коде стал бы дырой изоляции.
3. **Каждый вход и каждая мутация пишутся в журнал** (``ad_interactions``,
   ``actor='owner'``). Полный доступ означает, что владелец может создать заказ
   и написать в чат ОТ ИМЕНИ клиента — такие записи обязаны быть отличимы от
   действий самого клиента, иначе таймлайн начинает врать о том, кто что сделал.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from sqlalchemy import select

from database.models import AdClient
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

#: Имя query-параметра, которым владелец выбирает кабинет.
PARAM = "as_client"

#: ``kind`` записей журнала. Отдельные виды для входа и для мутации: вход
#: массовый и служит следом «кто смотрел», мутация — редкая и служит ответом на
#: вопрос «кто это сделал, клиент или владелец».
KIND_ENTER = "owner_cabinet_enter"
KIND_ACTION = "owner_cabinet_action"

#: Методы, которые меняют состояние. GET/HEAD/OPTIONS журналируем только входом.
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_owner(user) -> bool:
    """Владелец ли аккаунт. Делегирует единственному источнику истины."""
    from middleware.auth_gate import is_owner_account

    return is_owner_account(user)


def requested_client_id(request) -> Optional[int]:
    """``as_client`` из query. ``None`` — параметра нет.

    Мусорное значение (не число) считаем отсутствием параметра, а не нулём:
    иначе опечатка в адресной строке тихо увела бы владельца «в кабинет №0».
    """
    raw = (request.query_params.get(PARAM) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("impersonation: нечисловой %s=%r — игнорируем", PARAM, raw[:40])
        return None
    return value if value > 0 else None


async def resolve_target(session, user, request) -> Optional[AdClient]:
    """Карточка, в которую владелец просит зайти. ``None`` — обычный запрос.

    Поднимает 403, если ``as_client`` пришёл от НЕ владельца, и 404, если такой
    карточки нет. Молчаливого игнорирования тут быть не должно (см. свойство 2
    в докстринге модуля).
    """
    from fastapi import HTTPException

    client_id = requested_client_id(request)
    if client_id is None:
        return None

    if not is_owner(user):
        logger.warning(
            "impersonation: отказ — user %s не владелец, просил client_id=%s",
            getattr(user, "id", None),
            client_id,
        )
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    client = (
        await session.execute(select(AdClient).where(AdClient.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail=f"Клиент {client_id} не найден")
    return client


def audit(session, *, user, client: AdClient, request) -> None:
    """Записать в журнал вход владельца или его действие от имени клиента.

    Без commit — коммитит вызывающий эндпоинт в своей транзакции (конвенция
    ``interaction_log``). GET'ы пишутся как вход, мутации — отдельным видом.
    """
    method = (getattr(request, "method", "") or "GET").upper()
    mutating = method in _MUTATING
    path = getattr(getattr(request, "url", None), "path", "") or ""
    log_interaction(
        session,
        kind=KIND_ACTION if mutating else KIND_ENTER,
        summary=(
            f"владелец {method} {path} от имени клиента #{client.id}"
            if mutating
            else f"владелец открыл кабинет клиента #{client.id}"
        ),
        client_id=client.id,
        meta={
            "owner_user_id": getattr(user, "id", None),
            "owner_login": getattr(user, "login", None),
            "method": method,
            "path": path,
        },
        actor="owner",
    )


async def resolve(session, user, request) -> Tuple[Optional[AdClient], bool]:
    """Единая точка: (карточка-цель | None, было ли это импресонацией).

    Возвращает ``(None, False)`` для обычного запроса клиента — тогда вызывающий
    резолвит карточку как раньше, через сессию.
    """
    target = await resolve_target(session, user, request)
    if target is None:
        return None, False
    audit(session, user=user, client=target, request=request)
    return target, True
