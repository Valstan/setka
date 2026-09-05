"""Заказы кабинета рекламодателя: районы → отложки, цена, модерация.

Клиентский флоу — тонкая обёртка над существующим циклом операторского
планировщика: N строк ``AdScheduledPost`` (по одной на район) → VK-отложка
через ``VKPublisher.publish_bulletin(publish_date=…)`` → фиксацию выхода делает
``publish_reconciler`` (он же создаёт ``AdPublication`` и ``AdPayment(awaiting)``
по ``client_id``+``price`` — клиентский флоу СВОИХ платежей не создаёт).

Деньги: цену заказа считает ТОЛЬКО сервер — ``pricing.quote_for_client``
(``config/ad_landing.py``); ``price_split`` раскладывает её по строкам с
копеечным инвариантом ``Σ = total``, чтобы ``spent`` баланса сходился с прайсом.

Модерация (решение владельца 2026-08-25): не-``trusted`` клиент создаёт посты в
``status='pending'`` — в VK не уходит НИЧЕГО до одобрения владельцем. После
``AD_TRUST_AFTER_ORDERS`` одобренных заказов клиент становится ``trusted``.

VK-детали (publisher, заливка фото) инъектируются — логика тестируется без сети
(паттерн ``publish_reconciler``).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import String, func, select

from database.models import AdClient, AdScheduledPost, Region

logger = logging.getLogger(__name__)

# Порог доверия: сколько одобренных ЗАКАЗОВ делает клиента trusted (аудит
# 2026-09-05: считали посты — один заказ на три района сразу давал доверие).
# Старое имя env оставлено как запасное.
TRUST_AFTER_ORDERS = int(os.getenv("AD_TRUST_AFTER_ORDERS", os.getenv("AD_TRUST_AFTER_POSTS", "3")))
TRUST_AFTER_POSTS = TRUST_AFTER_ORDERS  # обратная совместимость (тесты, доки)

# Лимиты клиентского флоу (анти-флуд; VK-отложка вмещает ~150 постов на группу).
TEXT_MAX = int(os.getenv("AD_CLIENT_TEXT_MAX", "4000"))
MAX_ACTIVE_POSTS = int(os.getenv("AD_CLIENT_MAX_ACTIVE_POSTS", "30"))
# Безлимит (Этап 2): 38 сообществ × 30 дней — потолок незавершённых постов выше.
MAX_ACTIVE_POSTS_UNLIMITED = int(os.getenv("AD_CLIENT_MAX_ACTIVE_POSTS_UNLIMITED", "500"))
MAX_ORDERS_PER_DAY = int(os.getenv("AD_CLIENT_MAX_ORDERS_PER_DAY", "5"))

# «Опубликовать сразу» = та же отложка на ближайшее будущее: один пайплайн,
# фиксация — реконсилером. VK требует publish_date в будущем.
PUBLISH_NOW_DELAY = timedelta(minutes=3)
# Окно планирования клиентом.
SCHEDULE_MIN_AHEAD = timedelta(minutes=15)
SCHEDULE_MAX_AHEAD = timedelta(days=60)

# Статусы, считающиеся «активными» для лимита MAX_ACTIVE_POSTS.
ACTIVE_STATUSES = ("pending", "scheduled")

# Долговой гейт для trusted-клиентов (аудит 2026-09-05): trusted публиковался
# бесконтрольно — до 150 неоплаченных постов в неделю при полном молчании
# системы. Накопленный awaiting выше лимита (₽) или старше AD_DEBTOR_DAYS
# возвращает заказы на одобрение владельцу (не блокирует — владелец решает).
TRUST_DEBT_LIMIT_RUB = int(os.getenv("AD_TRUST_DEBT_LIMIT", "2000"))


async def debt_hold_reason(session, client: AdClient, *, now_utc: datetime) -> Optional[str]:
    """Почему заказ trusted-клиента уходит на одобрение: сумма или возраст долга.

    ``None`` — долга нет или он в пределах. Считается по ``ad_payments`` со
    ``status='awaiting'`` (единственный источник денежного требования).
    """
    from database.models import AdPayment
    from modules.ad_cabinet.debtors import DEBTOR_DAYS

    total, oldest = (
        await session.execute(
            select(
                func.coalesce(func.sum(AdPayment.amount), 0), func.min(AdPayment.created_at)
            ).where(AdPayment.client_id == client.id, AdPayment.status == "awaiting")
        )
    ).one()
    total = float(total or 0)
    if total <= 0:
        return None
    if TRUST_DEBT_LIMIT_RUB > 0 and total > TRUST_DEBT_LIMIT_RUB:
        return f"неоплаченных постов на {total:.0f} ₽ (лимит {TRUST_DEBT_LIMIT_RUB} ₽)"
    if oldest is not None and oldest <= now_utc - timedelta(days=DEBTOR_DAYS):
        days = (now_utc - oldest).days
        return f"есть неоплаченный пост старше {days} дн. (порог {DEBTOR_DAYS})"
    return None


# Инъектируемые фабрики: async publisher_factory(group_id) -> объект с
# publish_bulletin(...); attachment_builder(group_id, image_paths) -> ["photo…"].
PublisherFactory = Callable[[int], Awaitable[Any]]
AttachmentBuilder = Callable[[int, Sequence[str]], List[str]]


class OrderError(ValueError):
    """Ошибка валидации заказа — текст безопасен для показа клиенту."""


async def resolve_targets(session, region_ids: Sequence[int]) -> List[Tuple[int, int, str]]:
    """Районы заказа → [(region_id, community_vk_id, region_name)].

    Только активные регионы с ``vk_group_id`` (критерий ``get_vk_links``):
    заготовка района без группы клиенту недоступна. ``community_vk_id`` —
    отрицательный owner_id, как всюду в ad-CRM. Неизвестный/неактивный район —
    ошибка всего заказа, а не молчаливый пропуск.
    """
    wanted = sorted({int(r) for r in region_ids})
    if not wanted:
        raise OrderError("Не выбран ни один район")
    rows = (
        (
            await session.execute(
                select(Region.id, Region.vk_group_id, Region.name).where(
                    Region.id.in_(wanted),
                    Region.is_active.is_(True),
                    Region.vk_group_id.isnot(None),
                )
            )
        )
        .tuples()
        .all()
    )
    found = {rid for rid, _, _ in rows}
    missing = [r for r in wanted if r not in found]
    if missing:
        raise OrderError(f"Районы недоступны для размещения: {missing}")
    return [(rid, -abs(int(gid)), name or f"район {rid}") for rid, gid, name in rows]


async def all_target_region_ids(session) -> List[int]:
    """Все районы, доступные для «всей сети» (пакет): активные с группой."""
    rows = (
        await session.execute(
            select(Region.id).where(
                Region.is_active.is_(True),
                Region.vk_group_id.isnot(None),
            )
        )
    ).all()
    return [r[0] for r in rows]


def price_split(total: Decimal | int | float, n: int) -> List[Decimal]:
    """Разложить цену заказа по ``n`` строкам. Инвариант: ``Σ = total``.

    Остаток копеек — первой строке: баланс (``spent`` = Σ цен публикаций) и
    awaiting-оплаты реконсилера сходятся с прайсом копейка в копейку.
    """
    if n <= 0:
        return []
    total_d = Decimal(str(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    base = (total_d / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    parts = [base] * n
    drift = total_d - base * n
    parts[0] = (parts[0] + drift).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return parts


def validate_publish_at(
    publish_at: Optional[datetime], *, publish_now: bool, now: datetime
) -> datetime:
    """Момент публикации (naive МСК wall-clock) с проверкой окна планирования."""
    if publish_now:
        return now + PUBLISH_NOW_DELAY
    if publish_at is None:
        raise OrderError("Укажите дату публикации или выберите «опубликовать сразу»")
    if publish_at < now + SCHEDULE_MIN_AHEAD:
        raise OrderError("Дата публикации слишком близко — минимум через 15 минут")
    if publish_at > now + SCHEDULE_MAX_AHEAD:
        raise OrderError("Дата публикации слишком далеко — максимум 60 дней вперёд")
    return publish_at


async def check_client_limits(session, client: AdClient, *, now_utc: datetime) -> None:
    """Анти-флуд: активные посты и заказы за сутки.

    Сравнение по ``created_at`` — он наивный UTC (конвенция моделей), поэтому
    и граница суток строится от UTC-«сейчас», не от МСК wall-clock.
    """
    active = (
        await session.execute(
            select(func.count())
            .select_from(AdScheduledPost)
            .where(
                AdScheduledPost.client_id == client.id,
                AdScheduledPost.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalar_one()
    from modules.ad_cabinet import packages as _pkgs

    cap = (
        MAX_ACTIVE_POSTS_UNLIMITED
        if await _pkgs.has_unlimited(
            session, client.id, today=(now_utc + timedelta(hours=3)).date()
        )
        else MAX_ACTIVE_POSTS
    )
    if active >= cap:
        raise OrderError(
            f"Достигнут лимит незавершённых постов ({cap}) — "
            "дождитесь публикации или отмените лишние"
        )
    day_ago = now_utc - timedelta(days=1)
    orders_today = (
        await session.execute(
            select(func.count(func.distinct(AdScheduledPost.order_ref))).where(
                AdScheduledPost.client_id == client.id,
                AdScheduledPost.order_ref.isnot(None),
                AdScheduledPost.created_at >= day_ago,
            )
        )
    ).scalar_one()
    if orders_today >= MAX_ORDERS_PER_DAY:
        raise OrderError(f"Достигнут суточный лимит заказов ({MAX_ORDERS_PER_DAY})")


async def _send_one(
    post: AdScheduledPost,
    *,
    publisher_factory: PublisherFactory,
    attachment_builder: AttachmentBuilder,
    image_paths: Sequence[str],
    msk_to_unix: Callable[[datetime], int],
) -> None:
    """Отправить одну строку в VK-отложку (attachments — свои на каждую группу).

    Ошибка VK не роняет заказ: строка остаётся ``failed`` с ``error_message``,
    остальные районы продолжают (паттерн операторского create_scheduled).
    """
    gid = int(post.community_vk_id)
    try:
        attachments = attachment_builder(gid, image_paths) if image_paths else []
        post.attachments = ",".join(attachments) if attachments else None
        publisher = await publisher_factory(gid)
        res = await publisher.publish_bulletin(
            group_id=gid,
            text=post.text or "",
            attachments=attachments or None,
            from_group=post.from_group,
            publish_date=msk_to_unix(post.publish_date),
            signed=post.signed,
        )
        if res.get("success"):
            post.status = "scheduled"
            post.vk_postponed_post_id = res.get("post_id")
        else:
            post.status = "failed"
            post.error_message = str(res.get("error") or "VK не принял пост")
    except Exception as e:  # noqa: BLE001 - одна группа не роняет остальные
        logger.warning("client-order: VK send failed for %s: %s", gid, e)
        post.status = "failed"
        post.error_message = str(e)


async def submit_order(
    session,
    *,
    client: AdClient,
    user_id: int,
    text: str,
    image_paths: Sequence[str],
    region_ids: Sequence[int],
    publish_at: Optional[datetime],
    publish_now: bool,
    publisher_factory: PublisherFactory,
    attachment_builder: AttachmentBuilder,
    msk_to_unix: Callable[[datetime], int],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Создать заказ: N строк отложки, цена сервера, модерационный гейт.

    ``trusted`` → сразу в VK-отложку; иначе ``pending`` без единого VK-вызова.
    Commit — на вызывающем.

    Две шкалы времени (конвенция проекта): ``publish_date`` — МСК wall-clock
    (``now`` этого модуля), ``created_at`` — наивный UTC (дефолт модели).
    """
    now = now or datetime.utcnow() + timedelta(hours=3)  # МСК wall-clock naive
    text = (text or "").strip()
    if not text and not image_paths:
        raise OrderError("Пост пуст — добавьте текст или фото")
    if len(text) > TEXT_MAX:
        raise OrderError(f"Текст длиннее лимита {TEXT_MAX} символов")

    await check_client_limits(session, client, now_utc=now - timedelta(hours=3))
    targets = await resolve_targets(session, region_ids)
    when = validate_publish_at(publish_at, publish_now=publish_now, now=now)

    from modules.ad_cabinet import packages as pkgs

    # Анти-спам: один рекламный пост клиента в одно сообщество в один
    # календарный день МСК (в разные районы — сколько угодно в рамках пакета;
    # раскладку по дням даёт publish_at).
    busy = await pkgs.busy_days(session, client.id, targets, when.date())
    if busy:
        names = [t[2] for t in targets if t[1] in set(busy)]
        raise OrderError(
            "На этот день у вас уже есть пост в: "
            + ", ".join(names)
            + " — не больше одного рекламного поста в сообщество в день; "
            "выберите другую дату для этих районов"
        )

    # Пакеты (решения владельца 2026-08-26): долг/исчерпанный месяц — блок;
    # доступный пакет — заказ ТОЛЬКО в счёт пакета, сверх остатка — отказ.
    state = await pkgs.get_state(session, client.id, today=now.date())
    if state["block_reason"]:
        raise OrderError(state["block_reason"])
    package = state["package"]
    if package is not None:
        left = pkgs.remaining(package)  # безлимит — без квоты (Этап 2)
        if len(targets) > left:
            raise OrderError(
                f"В пакете осталось {left} постов, а районов выбрано {len(targets)} — "
                "уменьшите выбор или договоритесь с владельцем о расширении"
            )
        # Месячный пакет тратится на публикации ВНУТРИ месяца: отложка за
        # период — это перенос квоты через границу, не «10 постов в месяц»
        # (should-fix ревью 2026-08-26).
        if package.period_end is not None and not pkgs.period_covers(package, when.date()):
            raise OrderError(
                f"Пакет действует до {package.period_end.isoformat()} — "
                "выберите дату публикации внутри периода пакета"
            )
        if not await pkgs.consume(session, package, len(targets)):
            raise OrderError(
                "Остаток пакета уже израсходован параллельным заказом — обновите страницу"
            )
        quote = {"n": len(targets), "price": 0, "package_id": package.id, "kind": package.kind}
        prices = [Decimal("0")] * len(targets)
    else:
        # Прайс → скидки клиента → пол (Этап 2, 2026-09-05): та же функция, что
        # у котировки кабинета и бота — клиент платит ровно то, что видел.
        from modules.ad_cabinet.pricing import quote_for_client

        quote = await quote_for_client(session, client.id, len(targets), now_msk=now)
        prices = price_split(Decimal(quote["price"]), len(targets))
    order_ref = str(uuid.uuid4())
    # Долговой гейт: trusted с долгом сверх лимита/срока — снова на одобрение.
    debt_reason = (
        await debt_hold_reason(session, client, now_utc=now - timedelta(hours=3))
        if client.trusted
        else None
    )
    trusted = bool(client.trusted) and debt_reason is None

    rows: List[AdScheduledPost] = []
    for (region_id, gid, _name), price in zip(targets, prices):
        row = AdScheduledPost(
            community_vk_id=gid,
            region_id=region_id,
            text=text,
            image_names=list(image_paths) or [],
            publish_date=when,
            from_group=True,
            signed=False,
            comments_enabled=True,
            status="pending" if not trusted else "draft",
            client_id=client.id,
            price=price,
            created_by_user_id=user_id,
            order_ref=order_ref,
            package_id=package.id if package is not None else None,
        )
        session.add(row)
        rows.append(row)

    if trusted:
        for row in rows:
            await _send_one(
                row,
                publisher_factory=publisher_factory,
                attachment_builder=attachment_builder,
                image_paths=image_paths,
                msk_to_unix=msk_to_unix,
            )
            if row.status == "failed":
                await pkgs.refund_post(session, row)  # VK не принял — вернуть в пакет

    return {
        "order_ref": order_ref,
        "price_total": float(quote["price"]),
        "quote": quote,
        "posts": rows,
        "moderation": not trusted,
        # Причина, по которой trusted-клиент попал на одобрение (долг); None — обычный путь.
        "debt_hold": debt_reason,
        "package": package.to_dict() if package is not None else None,
    }


async def approve_post(
    session,
    post: AdScheduledPost,
    *,
    publisher_factory: PublisherFactory,
    attachment_builder: AttachmentBuilder,
    msk_to_unix: Callable[[datetime], int],
    now: Optional[datetime] = None,
    new_publish_at: Optional[datetime] = None,
) -> AdScheduledPost:
    """Владелец одобряет pending-пост: отправка в VK + счётчик доверия.

    Идемпотентно: не-``pending`` пост возвращается как есть (повторный клик
    «Одобрить» не публикует второй раз). Прошедшая дата публикации **не
    переносится молча** (аудит 2026-09-05: пост, заказанный на субботнее утро
    и одобренный в среду, выходил через три минуты): нужен явный
    ``new_publish_at``, иначе ``OrderError``. Commit — на вызывающем.
    """
    if post.status != "pending":
        return post
    now = now or datetime.utcnow() + timedelta(hours=3)
    if new_publish_at is not None:
        if new_publish_at <= now + timedelta(minutes=1):
            raise OrderError("Новая дата публикации должна быть в будущем")
        post.publish_date = new_publish_at
    elif post.publish_date <= now + timedelta(minutes=1):
        raise OrderError(
            f"Дата публикации {post.publish_date:%d.%m %H:%M} уже прошла — "
            "укажите новую дату при одобрении"
        )
    await _send_one(
        post,
        publisher_factory=publisher_factory,
        attachment_builder=attachment_builder,
        image_paths=list(post.image_names or []),
        msk_to_unix=msk_to_unix,
    )
    post.moderated_at = datetime.utcnow()  # таймстампы моделей — наивный UTC
    if post.status == "failed":
        from modules.ad_cabinet import packages as pkgs

        await pkgs.refund_post(session, post)  # одобрение не дошло до VK

    if post.status == "scheduled" and post.client_id:
        client = await session.get(AdClient, post.client_id)
        if client is not None:
            client.approved_posts_count = (client.approved_posts_count or 0) + 1
            if not client.trusted:
                await session.flush()
                orders = await approved_orders_count(session, client.id)
                if orders >= TRUST_AFTER_ORDERS:
                    client.trusted = True
                    logger.info(
                        "client-order: client %s promoted to trusted after %s approved orders",
                        client.id,
                        orders,
                    )
    return post


async def approved_orders_count(session, client_id: int) -> int:
    """Сколько РАЗНЫХ заказов клиента дошло до VK (scheduled/published).

    Строки без ``order_ref`` (старые, операторские) считаются по одной.
    """
    key = func.coalesce(AdScheduledPost.order_ref, func.cast(AdScheduledPost.id, String))
    return int(
        (
            await session.execute(
                select(func.count(func.distinct(key))).where(
                    AdScheduledPost.client_id == int(client_id),
                    AdScheduledPost.status.in_(("scheduled", "published")),
                )
            )
        ).scalar_one()
        or 0
    )


async def reject_post(session, post: AdScheduledPost, *, comment: str) -> AdScheduledPost:
    """Владелец отклоняет pending-пост; причина видна клиенту в кабинете.

    Пост возвращается в пакет: списание — только за реальные размещения.
    """
    if post.status != "pending":
        return post
    post.status = "rejected"
    post.moderation_comment = (comment or "").strip() or None
    post.moderated_at = datetime.utcnow()  # таймстампы моделей — наивный UTC
    from modules.ad_cabinet import packages as pkgs

    await pkgs.refund_post(session, post)
    return post
