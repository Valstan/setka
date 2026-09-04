"""Пакеты постов клиента кабинета: доступность, списание, возврат, блокировки.

Правила (решения владельца 2026-08-26):

* Есть доступный пакет → заказ идёт ТОЛЬКО в счёт пакета (посты с ``price=0``),
  сверх остатка — отказ, а не доплата.
* Периодный пакет (месячный) с исчерпанным остатком → создание постов
  заблокировано до конца периода («на тот месяц, на который куплен пакет»).
* ``postpaid`` с неоплаченным ИСТЕКШИМ периодом → блок всех заказов, пока
  владелец не отметит оплату или не продлит в долг. Авто-продления нет.
* ``prepaid`` доступен только после галочки владельца (``paid_at``).
* Исчерпанный БЕССРОЧНЫЙ пакет — не блок: клиент возвращается на общий прайс.
* Отменённый/отклонённый/не дошедший до VK пост возвращается в пакет.

Анти-спам (независим от пакетов): от одного клиента в одно сообщество — не
больше одного рекламного поста в один календарный день МСК. Календарный день,
а не скользящие сутки: «вечером один, утром следующего дня ещё один» — законно.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select

from database.models import AdClientPackage, AdPayment, AdScheduledPost

#: Провайдер платежа, которым помечаются деньги за пакет (миграция 097).
PACKAGE_PAYMENT_PROVIDER = "package"


async def record_package_payment(session, pkg: AdClientPackage) -> Optional[AdPayment]:
    """Деньги за пакет → строка ``ad_payments`` (аудит 2026-09-05).

    До этого оплата пакета жила только в ``ad_client_packages.price`` и не
    попадала ни в баланс клиента, ни в «оплачено» списка кабинетов: клиент,
    заплативший 5000 ₽ за пакет, выглядел как «вышло 10 · оплачено 0».
    Один платёж на пакет: ``(provider='package', external_id=str(pkg.id))`` —
    уникум ``uq_ad_payments_provider_ext`` (083). Бесплатный промо и нулевая
    цена платежа не порождают. ``units_paid=posts_total`` — штучный учёт
    перерасхода продолжает работать. Commit — на вызывающем.
    """
    if pkg.paid_at is None or pkg.kind == "free_promo" or float(pkg.price or 0) <= 0:
        return None
    ext = str(int(pkg.id))
    existing = (
        await session.execute(
            select(AdPayment).where(
                AdPayment.provider == PACKAGE_PAYMENT_PROVIDER, AdPayment.external_id == ext
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    pay = AdPayment(
        client_id=pkg.client_id,
        amount=pkg.price,
        status="paid",
        units_paid=int(pkg.posts_total) if pkg.posts_total else None,
        provider=PACKAGE_PAYMENT_PROVIDER,
        external_id=ext,
        note=f"пакет #{pkg.id} ({pkg.kind})",
        paid_at=pkg.paid_at,
        paid_confirmed_at=pkg.paid_at,
    )
    session.add(pay)
    await session.flush()
    return pay


logger = logging.getLogger(__name__)

# Акция «бесплатная реклама местным» (решение владельца 2026-08-26): 3 поста.
PROMO_POSTS = 3

KINDS = ("free_promo", "prepaid", "postpaid")

# Статусы, занимающие дневной слот сообщества и место в пакете.
_SLOT_STATUSES = ("pending", "scheduled", "published")


def _remaining(pkg: AdClientPackage) -> int:
    return max(0, int(pkg.posts_total or 0) - int(pkg.posts_used or 0))


def _period_covers(pkg: AdClientPackage, today: date) -> bool:
    if pkg.period_start and today < pkg.period_start:
        return False
    if pkg.period_end and today > pkg.period_end:
        return False
    return True


def period_covers(pkg: AdClientPackage, day: date) -> bool:
    """Публичная проверка «день внутри периода пакета» (для даты публикации)."""
    return _period_covers(pkg, day)


def _is_available(pkg: AdClientPackage, today: date) -> bool:
    """Можно ли списывать с пакета сегодня."""
    if not pkg.is_active or _remaining(pkg) <= 0:
        return False
    if not _period_covers(pkg, today):
        return False
    if pkg.kind == "prepaid" and pkg.paid_at is None:
        return False
    return True


async def get_state(session, client_id: int, *, today: Optional[date] = None) -> Dict[str, Any]:
    """Состояние пакетов клиента для enforcement и API.

    Возвращает::

        {
          "block_reason": str | None,   # заказы запрещены целиком
          "package": AdClientPackage | None,  # доступный для списания
          "packages": [AdClientPackage, ...], # все активные (для UI)
        }

    Порядок списания: сначала бесплатные промо, затем старейшие платные.
    """
    today = today or (datetime.utcnow() + timedelta(hours=3)).date()  # МСК
    rows: List[AdClientPackage] = (
        (
            await session.execute(
                select(AdClientPackage)
                .where(
                    AdClientPackage.client_id == client_id,
                    AdClientPackage.is_active.is_(True),
                )
                .order_by(AdClientPackage.id.asc())
            )
        )
        .scalars()
        .all()
    )

    # 1. Доступный пакет побеждает долг: «могу вручную продлить, даже если не
    #    оплатил» — продление создаёт новый пакет, и он ОБЯЗАН снимать блок
    #    (блокер adversarial-ревью 2026-08-26: иначе документированный путь
    #    разблокировки мёртв). Долг при этом остаётся виден владельцу.
    available = [p for p in rows if _is_available(p, today)]
    if available:
        promo = [p for p in available if p.kind == "free_promo"]
        return {
            "block_reason": None,
            "package": (promo or available)[0],
            "packages": rows,
        }

    # 2. Долг: postpaid с истёкшим периодом без галочки оплаты — блок всего.
    for pkg in rows:
        if (
            pkg.kind == "postpaid"
            and pkg.paid_at is None
            and pkg.period_end is not None
            and today > pkg.period_end
        ):
            return {
                "block_reason": (
                    "Прошлый пакет не оплачен — создание постов приостановлено, "
                    "свяжитесь с владельцем в чате"
                ),
                "package": None,
                "packages": rows,
            }

    # 3. Текущий ПЕРИОДНЫЙ пакет исчерпан → блок до конца периода.
    for pkg in rows:
        if (
            pkg.period_end is not None
            and _period_covers(pkg, today)
            and _remaining(pkg) <= 0
            and (pkg.kind != "prepaid" or pkg.paid_at is not None)
        ):
            return {
                "block_reason": (
                    f"Пакет на этот период исчерпан ({pkg.posts_total} постов) — "
                    f"новые посты откроются после {pkg.period_end.isoformat()} "
                    "или по договорённости с владельцем"
                ),
                "package": None,
                "packages": rows,
            }

    # 4. prepaid, ждущий галочки оплаты, — заказы по общему прайсу не блокируем,
    #    но и пакет недоступен (клиент видит его статус в кабинете).
    return {"block_reason": None, "package": None, "packages": rows}


async def consume(session, pkg: AdClientPackage, n: int) -> bool:
    """Атомарно списать ``n`` постов; False — остатка не хватило.

    Guarded UPDATE вместо read-modify-write: два параллельных сабмита (двойной
    клик) не растратят последний слот дважды (should-fix ревью 2026-08-26).
    """
    from sqlalchemy import update

    result = await session.execute(
        update(AdClientPackage)
        .where(
            AdClientPackage.id == pkg.id,
            AdClientPackage.posts_used + int(n) <= AdClientPackage.posts_total,
        )
        .values(posts_used=AdClientPackage.posts_used + int(n))
    )
    if result.rowcount == 0:
        return False
    await session.refresh(pkg)
    return True


async def refund_post(session, post: AdScheduledPost) -> None:
    """Вернуть пост в пакет при терминальном не-published исходе.

    Идемпотентен по конструкции: ``package_id`` обнуляется после декремента —
    второй вызов по тому же посту (например, cancel уже-failed поста) не
    вернёт слот дважды (блокер adversarial-ревью 2026-08-26). Декремент —
    атомарный guarded UPDATE, ниже нуля не уходит.
    """
    if not post.package_id:
        return
    from sqlalchemy import update

    await session.execute(
        update(AdClientPackage)
        .where(AdClientPackage.id == post.package_id, AdClientPackage.posts_used > 0)
        .values(posts_used=AdClientPackage.posts_used - 1)
    )
    post.package_id = None


async def busy_days(
    session,
    client_id: int,
    targets: Sequence[Tuple[int, int]],
    publish_day: date,
) -> List[int]:
    """Сообщества из ``targets``, где у клиента УЖЕ есть пост на этот день.

    Анти-спам: один рекламный пост от клиента в одно сообщество в один
    календарный день МСК (pending/scheduled/published; отменённые и отклонённые
    день не занимают).
    """
    gids = [t[1] for t in targets]  # (region_id, gid[, name]) — gid всегда вторым
    if not gids:
        return []
    day_start = datetime(publish_day.year, publish_day.month, publish_day.day)
    day_end = day_start + timedelta(days=1)
    rows = (
        await session.execute(
            select(AdScheduledPost.community_vk_id, func.count())
            .where(
                AdScheduledPost.client_id == client_id,
                AdScheduledPost.community_vk_id.in_(gids),
                AdScheduledPost.status.in_(_SLOT_STATUSES),
                AdScheduledPost.publish_date >= day_start,
                AdScheduledPost.publish_date < day_end,
            )
            .group_by(AdScheduledPost.community_vk_id)
        )
    ).all()
    return [int(gid) for gid, cnt in rows if cnt > 0]
