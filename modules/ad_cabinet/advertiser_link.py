"""Линковка «аккаунт ЕСА → карточка клиента CRM» для кабинета рекламодателя.

Кабинет — клиентское окно в существующий ad-CRM: залогиненный через ЕСА юзер
(`radar_users`) должен попадать ровно в СВОЮ карточку `ad_clients`. Связь
держится на двух ключах, оба — identity, не ручной ввод:

  1. явный FK ``ad_clients.radar_user_id`` (миграция 081) — источник правды;
  2. fallback: ``radar_users.vk_user_id == ad_clients.author_vk_id`` — клиент,
     которого оператор давно ведёт в CRM по его VK id, входит через ВК и
     получает свою же карточку. При совпадении FK записывается (self-healing,
     одноразово) — дальше работает путь 1.

Линковать по логину/email/телефону НЕЛЬЗЯ: эти поля вводятся руками (клиентом
при регистрации, оператором в карточке) и позволили бы присвоить чужую карточку
подбором. VK id приходит от ВКонтакте, пользователь его не выбирает.

Самозавод (онбординг): у юзера нет карточки → создаётся новая пустая
(``trusted=False`` — модерация новых клиентов, решение владельца 2026-08-25).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from database.models import AdClient

logger = logging.getLogger(__name__)

# Воронка самозаведённого клиента начинается с 'detected' — как у карточек
# из предложки: клиент есть, сделки ещё нет.
ONBOARD_STAGE = "detected"


async def resolve_client(session, user) -> Optional[AdClient]:
    """Найти карточку клиента для аккаунта ЕСА. ``None`` — карточки нет.

    Порядок: явный FK → fallback по VK-identity (с бэкфиллом FK). Пишет только
    ``radar_user_id`` при fallback-совпадении; commit — на вызывающем.
    """
    row = (
        await session.execute(select(AdClient).where(AdClient.radar_user_id == user.id))
    ).scalar_one_or_none()
    if row is not None:
        return row

    vk_id = getattr(user, "vk_user_id", None)
    if not vk_id:
        return None
    row = (
        await session.execute(
            select(AdClient).where(
                AdClient.author_vk_id == int(vk_id),
                AdClient.radar_user_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        # Self-healing: одноразовый бэкфилл явного FK по VK-identity.
        row.radar_user_id = user.id
        logger.info(
            "advertiser-link: client %s linked to radar_user %s via vk_id %s",
            row.id,
            user.id,
            vk_id,
        )
    return row


async def onboard_client(
    session,
    user,
    *,
    name: Optional[str] = None,
    phone: Optional[str] = None,
) -> AdClient:
    """Карточка для онбординга: существующая (с линковкой) либо новая.

    Новая карточка: ``trusted=False`` (первые посты — через одобрение
    владельца), контакты — только заявленные юзером поля своей карточки.
    Commit — на вызывающем.
    """
    row = await resolve_client(session, user)
    if row is not None:
        return row

    vk_id = getattr(user, "vk_user_id", None)
    row = AdClient(
        radar_user_id=user.id,
        author_vk_id=int(vk_id) if vk_id else None,
        name=(name or "").strip() or getattr(user, "display_name", None),
        phone=(phone or "").strip() or None,
        stage=ONBOARD_STAGE,
        trusted=False,
    )
    session.add(row)
    logger.info("advertiser-link: new client onboarded for radar_user %s", user.id)
    return row
