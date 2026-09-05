"""Планировщик предложки: оригинал с подписью автора + репосты в соседей.

Заказ владельца 2026-09-05 (Этап 0 программы «Кабинет под ключ»): взять
предложенный пост (``ad_requests`` origin='suggested'), назначить дату выхода,
отметить сообщества-дублёры — и чтобы оно само вышло везде «в то же время».

Решение владельца по идентичности: в исходном сообществе публикуется САМ
предложенный пост с подписью «Предложил(а): …» (``VKPublisher.publish_suggested``
— ``wall.post post_id=<suggest> signed=1``, только user-токеном админа); в
дублёры уходит ``wall.repost`` этой записи от имени сообщества-дублёра ПОСЛЕ
фактического выхода оригинала — этим занимается ``repost_dispatcher``.

Модель — без новой таблицы: каждое размещение (оригинал + каждый дублёр) —
обычная строка ``AdScheduledPost`` с ``client_id``/``price`` (миграция 095:
``kind``, ``source_post_id``, ``next_attempt_at``, ``attempts``). Тогда счётчики
кабинетов, реконсилер, отмена, возврат в пакет и awaiting-платёж работают без
второго журнала.

Режимы оригинала (``mode``): ``vk_postpone`` — VK-отложка (``publish_date`` в
самом вызове); ``queue`` — VK не трогаем, оригинал публикует диспетчер в момент
``publish_at`` (fallback, если probe покажет, что отложка предложки теряет
подпись). Оба режима — один код диспетчера.

Цену считает только сервер: операторская цена на весь заказ, пол
``PLACEMENT_FLOOR_RUB`` за КАЖДОЕ размещение; ``price_split`` раскладывает по
строкам с инвариантом Σ=total. VK-детали инъектируются (publisher) — логика
тестируется без сети (паттерн ``client_orders``).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

from database.models import AdClient, AdRequest, AdScheduledPost, Region
from modules.ad_cabinet.client_orders import OrderError, price_split
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

# Пол цены за одно размещение (решение владельца 2026-09-05: «минимум для поста
# рублей двести»). Общая константа появится в Этапе 2 (config/ad_landing);
# пока — здесь, с тем же значением по умолчанию.
PLACEMENT_FLOOR_RUB = int(os.getenv("AD_SUGGESTED_FLOOR_RUB", "200"))

# Минимальный горизонт: VK требует publish_date в будущем; диспетчеру тоже
# нужен запас на тик.
MIN_AHEAD = timedelta(minutes=1)

MODE_VK_POSTPONE = "vk_postpone"
MODE_QUEUE = "queue"
MODES = (MODE_VK_POSTPONE, MODE_QUEUE)

Target = Tuple[int, int, str]  # (region_id, community_vk_id (отрицательный), name)


def _msk_to_unix(naive_msk: datetime) -> int:
    from datetime import timezone

    return int(naive_msk.replace(tzinfo=timezone(timedelta(hours=3))).timestamp())


async def default_dup_targets(session, region: Region) -> List[Target]:
    """Соседи региона по ``Region.neighbors`` (активные, с группой), без него самого."""
    from modules.cascaded_bulletin import _resolve_neighbor_regions

    rows = await _resolve_neighbor_regions(session, region)
    return [(int(r.id), -abs(int(r.vk_group_id)), r.name or r.code) for r in rows]


async def all_dup_candidates(session, region: Region) -> List[Dict[str, Any]]:
    """Все сообщества сети, куда можно дублировать, с флагом ``default`` у соседей."""
    neighbors = {gid for _, gid, _ in await default_dup_targets(session, region)}
    rows = (
        (
            await session.execute(
                select(Region).where(
                    Region.is_active.is_(True),
                    Region.vk_group_id.isnot(None),
                    Region.id != region.id,
                )
            )
        )
        .scalars()
        .all()
    )
    out = []
    for r in rows:
        gid = -abs(int(r.vk_group_id))
        out.append(
            {
                "region_id": int(r.id),
                "community_vk_id": gid,
                "code": r.code,
                "name": r.name or r.code,
                "default": gid in neighbors,
            }
        )
    out.sort(key=lambda x: (not x["default"], x["name"]))
    return out


async def resolve_dup_targets(
    session, region: Region, wanted_gids: Optional[Sequence[int]]
) -> List[Target]:
    """Сообщества-дублёры: явный список ``wanted_gids`` (любые активные регионы с
    группой, кроме исходного) либо соседи по умолчанию (``None``)."""
    if wanted_gids is None:
        return await default_dup_targets(session, region)
    wanted = sorted({-abs(int(g)) for g in wanted_gids})
    if not wanted:
        return []
    src_gid = -abs(int(region.vk_group_id)) if region.vk_group_id else None
    if src_gid in wanted:
        raise OrderError("Исходное сообщество нельзя выбрать дублёром")
    # На проде regions.vk_group_id хранится СО ЗНАКОМ МИНУС (owner_id), в части
    # фикстур — положительным; сравниваем по модулю на стороне SQL (поймано
    # владельцем 05.09 на первом же плане: «сообщества недоступны»).
    from sqlalchemy import func

    rows = (
        (
            await session.execute(
                select(Region).where(
                    Region.is_active.is_(True),
                    Region.vk_group_id.isnot(None),
                    func.abs(Region.vk_group_id).in_([abs(g) for g in wanted]),
                )
            )
        )
        .scalars()
        .all()
    )
    found = {-abs(int(r.vk_group_id)): r for r in rows}
    missing = [g for g in wanted if g not in found]
    if missing:
        raise OrderError(f"Сообщества недоступны для дублирования: {missing}")
    return [(int(r.id), gid, r.name or r.code) for gid, r in found.items()]


async def ensure_client(session, ar: AdRequest) -> AdClient:
    """Карточка клиента по ``author_vk_id`` заявки (ядро ``upsert_from_request``)."""
    key = ar.author_vk_id or ar.peer_id
    if not key:
        raise OrderError("У заявки нет автора (author_vk_id/peer_id) — клиента не завести")
    existing = (
        await session.execute(select(AdClient).where(AdClient.author_vk_id == int(key)))
    ).scalar_one_or_none()
    if existing is not None:
        client = existing
    else:
        client = AdClient(
            author_vk_id=int(key),
            author_is_group=bool(ar.author_is_group),
            name=ar.author_name,
            region_id=ar.region_id,
            stage="detected",
        )
        session.add(client)
        await session.flush()
        log_interaction(
            session,
            kind="detected",
            client_id=client.id,
            ad_request_id=ar.id,
            summary="Заведён клиент из заявки (планировщик предложки)",
        )
    if ar.client_id != client.id:
        ar.client_id = client.id
    return client


def total_price(price: Optional[Decimal | int | float], n: int) -> Decimal:
    """Цена заказа: пусто → пол×n; ниже пола×n — отказ; иначе как задал оператор."""
    floor_total = Decimal(PLACEMENT_FLOOR_RUB * n)
    if price is None:
        return floor_total
    total = Decimal(str(price)).quantize(Decimal("0.01"))
    if total < floor_total:
        raise OrderError(
            f"Цена {total:.0f} ₽ ниже минимума {floor_total:.0f} ₽ "
            f"({n} размещений × {PLACEMENT_FLOOR_RUB} ₽)"
        )
    return total


# ------------------------------------------------------- живая предложка
#
# Инцидент 2026-09-05: планировщик показывал только заявки, которые сканер
# счёл рекламой (порог классификатора 3), и два поста Анны Валиевой (балл 2 —
# только «номер телефона») в нём отсутствовали, хотя висели в предложке VK.
# Оператор, выбравший сообщество в планировщике, хочет видеть ВСЮ предложку —
# классификатор здесь не фильтр, а подсказка (score в карточке).


async def build_live_checker():
    """Чекер предложки из живых токенов (user-токен по READ-политике); None — нет токенов."""
    from modules.notifications.vk_suggested_checker import VKSuggestedChecker
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        return None
    return VKSuggestedChecker(user_token, community_tokens=community_tokens)


async def sync_live_suggests(
    session,
    region: Region,
    *,
    checker,
    classify_fn=None,
    insert_fn=None,
) -> Dict[str, Any]:
    """Подтянуть живую предложку сообщества в ``ad_requests`` перед показом.

    - посты, которых нет в базе, заводятся как ``new`` независимо от вердикта
      классификатора (score/reasons — справочно, плюс пометка «из планировщика»);
      ``can_message`` не заполняется — автоприветствие такие заявки не трогает;
    - заявки ``vanished``, чей пост снова в выдаче, возвращаются в ``new``;
    - ``skipped``/``published``/``deleted`` не трогаем — это решения оператора.

    VK не ответил → ``{"error": …}`` и никаких изменений: показываем базу.
    Без commit.
    """
    if classify_fn is None:
        from modules.ad_cabinet.classifier import classify as classify_fn  # noqa: N806
    if insert_fn is None:
        from modules.ad_cabinet.scanner import _insert_if_new as insert_fn  # noqa: N806

    gid = -abs(int(region.vk_group_id))
    out: Dict[str, Any] = {"fetched": 0, "inserted": 0, "revived": 0, "error": None}
    if checker is None:
        out["error"] = "нет VK-токена"
        return out
    try:
        posts = checker.fetch_suggested_posts(gid)
    except Exception as e:  # noqa: BLE001 - показ базы важнее
        out["error"] = str(e)[:200]
        return out
    err = getattr(checker, "last_fetch_error", None)
    if err:
        out["error"] = str(err)[:200]
        return out
    out["fetched"] = len(posts)
    live_ids = {int(p["vk_post_id"]) for p in posts if p.get("vk_post_id")}

    region_dict = {
        "region_id": region.id,
        "region_name": region.name,
        "region_code": region.code,
        "vk_group_id": region.vk_group_id,
    }
    for p in posts:
        try:
            _is_ad, score, reasons = await classify_fn(p)
        except Exception:  # noqa: BLE001
            score, reasons = 0, []
        marked = list(reasons) + ["из планировщика: вся предложка"]
        if await insert_fn(session, region_dict, p, score, marked):
            out["inserted"] += 1

    if live_ids:
        stale = (
            await session.execute(
                select(AdRequest).where(
                    AdRequest.community_vk_id == gid,
                    AdRequest.origin == "suggested",
                    AdRequest.status == "vanished",
                    AdRequest.vk_post_id.in_(list(live_ids)),
                )
            )
        ).scalars()
        for ar in stale:
            ar.status = "new"
            out["revived"] += 1
    return out


async def plan_item(
    session,
    ar: AdRequest,
    *,
    publish_at: datetime,
    price: Optional[Decimal | int | float],
    dup_targets: Sequence[Target],
    publisher,
    mode: str = MODE_VK_POSTPONE,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Запланировать одну заявку из предложки: оригинал + строки-дублёры.

    ``publish_at`` и ``now`` — naive МСК wall-clock. ``publisher`` — объект с
    ``publish_suggested`` (инъекция для тестов). Commit — на вызывающем.

    Идемпотентность: заявка со ``status='published'`` → ``{"already": True}``;
    вызывающий обязан держать строку заявки под ``FOR UPDATE`` (двойной клик).
    Провал VK на оригинале → строка ``failed``, дублёры не создаются, заявка
    остаётся ``new`` (можно переиграть).
    """
    if mode not in MODES:
        raise OrderError(f"Неизвестный режим планирования: {mode}")
    now = now or (datetime.utcnow() + timedelta(hours=3))
    if ar.status == "published":
        return {"already": True, "request_id": ar.id, "client_id": ar.client_id}
    if not ar.vk_post_id or not ar.community_vk_id:
        raise OrderError("У заявки нет предложенного поста (vk_post_id) или сообщества")
    if publish_at < now + MIN_AHEAD:
        raise OrderError("Дата публикации в прошлом или слишком близко")

    src_gid = -abs(int(ar.community_vk_id))
    dups = [t for t in dup_targets if t[1] != src_gid]
    n = 1 + len(dups)
    total = total_price(price, n)
    prices = price_split(total, n)

    client = await ensure_client(session, ar)

    # Анти-спам: один рекламный пост клиента в одно сообщество в календарный
    # день МСК — тот же гейт, что у клиентского заказа (packages.busy_days).
    from modules.ad_cabinet import packages as pkgs

    all_targets = [(ar.region_id or 0, src_gid, "исходное")] + list(dups)
    busy = await pkgs.busy_days(session, client.id, all_targets, publish_at.date())
    if busy:
        names = [t[2] for t in all_targets if t[1] in set(busy)]
        raise OrderError(
            "На этот день у клиента уже есть пост в: "
            + ", ".join(names)
            + " — не больше одного рекламного поста в сообщество в день"
        )

    order_ref = str(uuid.uuid4())
    original = AdScheduledPost(
        kind="suggested",
        community_vk_id=src_gid,
        region_id=ar.region_id,
        text=ar.text_snapshot or "",
        image_names=[],
        publish_date=publish_at,
        from_group=True,
        signed=True,
        comments_enabled=True,
        source_ad_request_id=ar.id,
        vk_postponed_post_id=int(ar.vk_post_id),
        client_id=client.id,
        price=prices[0],
        status="draft",
        order_ref=order_ref,
        attempts=0,
    )
    session.add(original)
    await session.flush()

    if mode == MODE_VK_POSTPONE:
        res = await publisher.publish_suggested(
            src_gid,
            int(ar.vk_post_id),
            signed=True,
            publish_date=_msk_to_unix(publish_at),
        )
        if not res.get("success"):
            original.status = "failed"
            original.error_message = str(res.get("error") or "VK не принял пост")[:500]
            logger.warning(
                "suggested-plan: VK refused request %s: %s", ar.id, original.error_message
            )
            return {
                "ok": False,
                "request_id": ar.id,
                "client_id": client.id,
                "original": original,
                "reposts": [],
                "error": original.error_message,
            }
        original.status = "scheduled"
        original.vk_postponed_post_id = int(res.get("post_id") or ar.vk_post_id)
    else:
        original.status = "scheduled"
        original.next_attempt_at = publish_at

    reposts: List[AdScheduledPost] = []
    for (region_id, gid, _name), line_price in zip(dups, prices[1:]):
        row = AdScheduledPost(
            kind="repost",
            source_post_id=original.id,
            community_vk_id=gid,
            region_id=region_id,
            text=ar.text_snapshot or "",
            image_names=[],
            publish_date=publish_at,
            next_attempt_at=publish_at,
            from_group=True,
            signed=False,
            comments_enabled=True,
            source_ad_request_id=ar.id,
            client_id=client.id,
            price=line_price,
            status="scheduled",
            order_ref=order_ref,
            attempts=0,
        )
        session.add(row)
        reposts.append(row)
    await session.flush()

    # Заявка обработана (прецедент B2): карточка уходит из «Новых». Оригинал из
    # предложки НЕ удаляем — он и есть будущий пост.
    ar.status = "published"
    if not ar.contacted_at:
        ar.contacted_at = datetime.utcnow()
    if client.stage in ("detected", "contacted"):
        client.stage = "scheduled"
    log_interaction(
        session,
        kind="scheduled",
        client_id=client.id,
        ad_request_id=ar.id,
        scheduled_post_id=original.id,
        summary=(
            f"Предложка запланирована на {publish_at:%d.%m %H:%M}: "
            f"{n} размещений, {total:.0f} ₽"
        ),
        meta={
            "placements": n,
            "price_total": float(total),
            "mode": mode,
            "dups": [gid for _, gid, _ in dups],
            "order_ref": order_ref,
        },
    )
    return {
        "ok": True,
        "request_id": ar.id,
        "client_id": client.id,
        "original": original,
        "reposts": reposts,
        "price_total": float(total),
        "order_ref": order_ref,
    }
