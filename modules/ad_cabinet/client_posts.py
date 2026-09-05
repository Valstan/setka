"""«📋 Мои посты» для ВК-бота: заказы клиента одним экраном (Этап 5, PR-3).

Чтение (``list_for_client``) отдельно от чистого рендера (``render``): бот и
любой другой канал показывают одно и то же. Группировка по ``order_ref`` (заказ
на N районов — один блок), имена районов, ссылка на вышедший пост из
``ad_publications`` (первична) либо по ``vk_postponed_post_id`` для
``published``; строки предложки/репостов (``kind``) помечаются. Длинный список
режется под лимит сообщения ВК и заканчивается ссылкой на кабинет.

Окно выборки идёт **по границе заказа, а не по строкам**: сначала дешёвым
запросом (id, order_ref) находим последние заказы, потом читаем строки только
их. Иначе заказ на всю сеть, разрезанный лимитом строк, печатал бы заниженные
число сообществ и сумму как настоящие — и без единого признака обрезки.

``publish_date`` — МСК naive, печатается как есть; ``created_at`` — UTC naive,
в заголовке заказа сдвигается на +3 ч.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

from database.models import AdPublication, AdScheduledPost, Region
from utils.text_utils import plural_ru

MSG_MAX = 4000  # запас до лимита ВК 4096 (как dialog.VK_MSG_MAX)
HEAD = "📋 Мои посты"
#: Сколько строк смотрим, чтобы посчитать заказы клиента (две колонки, дёшево).
SCAN_LIMIT = 2000
CABINET_URL = "https://сарафан.вмалмыже.рф/cabinet"
STATUS_RU = {
    "pending": "на одобрении",
    "draft": "черновик",
    "scheduled": "в очереди",
    "published": "вышел",
    "failed": "ошибка",
    "cancelled": "отменён",
    "rejected": "отклонён",
    "stalled": "завис, владелец разбирается",
}
KIND_RU = {"suggested": "предложка", "repost": "репост"}
MSK = timedelta(hours=3)


@dataclass
class PostView:
    id: int
    order_ref: Optional[str]
    region_name: str
    community_vk_id: int
    publish_date: Optional[datetime]
    status: str
    kind: str
    price: float
    moderation_comment: Optional[str]
    error_message: Optional[str]
    image_count: int
    vk_post_url: Optional[str]
    created_at: Optional[datetime]


def order_key(post_id: int, order_ref: Optional[str]) -> str:
    """Ключ группировки: заказ целиком либо одиночная строка. Чистая."""
    return order_ref or f"#{int(post_id)}"


async def list_for_client(
    session, client_id: int, *, max_orders: int = 10, scan_limit: int = SCAN_LIMIT
) -> Tuple[List[PostView], int, bool]:
    """``(строки последних заказов, сколько заказов не показано, точен ли счёт)``.

    Выбираются ТОЛЬКО целые заказы: половина заказа на экране печаталась бы как
    полный заказ с заниженными числом сообществ и суммой. ``scan_limit``
    ограничивает дешёвый скан ключей; упёрлись в него — счёт скрытых неточен
    (клиенту показываем «N+»).
    """
    keys = (
        (
            await session.execute(
                select(AdScheduledPost.id, AdScheduledPost.order_ref)
                .where(AdScheduledPost.client_id == int(client_id))
                .order_by(AdScheduledPost.id.desc())
                .limit(int(scan_limit))
            )
        )
        .tuples()
        .all()
    )
    if not keys:
        return [], 0, True
    order: List[str] = []
    ids_by_key: Dict[str, List[int]] = {}
    for rid, ref in keys:
        key = order_key(rid, ref)
        if key not in ids_by_key:
            ids_by_key[key] = []
            order.append(key)
        ids_by_key[key].append(int(rid))
    shown = order[: max(0, int(max_orders))]
    hidden = len(order) - len(shown)
    exact = len(keys) < int(scan_limit)
    ids = [i for k in shown for i in ids_by_key[k]]
    if not ids:
        return [], hidden, exact
    rows = (
        (
            await session.execute(
                select(
                    AdScheduledPost.id,
                    AdScheduledPost.order_ref,
                    AdScheduledPost.community_vk_id,
                    AdScheduledPost.publish_date,
                    AdScheduledPost.status,
                    AdScheduledPost.kind,
                    AdScheduledPost.price,
                    AdScheduledPost.moderation_comment,
                    AdScheduledPost.error_message,
                    AdScheduledPost.image_names,
                    AdScheduledPost.vk_postponed_post_id,
                    AdScheduledPost.created_at,
                    Region.name,
                )
                .outerjoin(Region, Region.id == AdScheduledPost.region_id)
                .where(AdScheduledPost.id.in_(ids))
                .order_by(AdScheduledPost.id.desc())
            )
        )
        .tuples()
        .all()
    )
    pubs: Dict[int, AdPublication] = {}
    for p in (
        await session.execute(
            select(AdPublication)
            .where(AdPublication.scheduled_post_id.in_(ids))
            .order_by(AdPublication.id.asc())
        )
    ).scalars():
        if p.scheduled_post_id is not None:
            pubs.setdefault(int(p.scheduled_post_id), p)
    out: List[PostView] = []
    for (
        rid,
        ref,
        gid,
        publish_date,
        status,
        kind,
        price,
        moderation_comment,
        error_message,
        image_names,
        postponed_id,
        created_at,
        region_name,
    ) in rows:
        pub = pubs.get(int(rid))
        url = None
        if pub is not None and pub.vk_post_id:
            url = f"https://vk.com/wall{pub.community_vk_id}_{pub.vk_post_id}"
        elif status == "published" and postponed_id:
            url = f"https://vk.com/wall{gid}_{postponed_id}"
        out.append(
            PostView(
                id=int(rid),
                order_ref=ref,
                region_name=region_name or f"сообщество {gid}",
                community_vk_id=int(gid),
                publish_date=publish_date,
                status=str(status or ""),
                kind=str(kind or "post"),
                price=float(price or 0),
                moderation_comment=moderation_comment,
                error_message=error_message,
                image_count=len(image_names or []),
                vk_post_url=url,
                created_at=created_at,
            )
        )
    return out, hidden, exact


def _money(v: float) -> str:
    return f"{float(v):,.0f} ₽".replace(",", " ")


def _group(views: Sequence[PostView]) -> List[List[PostView]]:
    """Группы по ``order_ref`` в порядке появления (список уже новые-первыми)."""
    groups: List[List[PostView]] = []
    index: Dict[str, int] = {}
    for v in views:
        key = order_key(v.id, v.order_ref)
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(v)
    return groups


def render_group(group: Sequence[PostView]) -> str:
    """Один заказ: заголовок с датой/числом сообществ/суммой и строка на район."""
    first = group[0]
    when = first.created_at + MSK if first.created_at else None
    head_date = f" от {when:%d.%m}" if when else ""
    n = len(group)
    total = sum(v.price for v in group)
    money = _money(total) if total else "в счёт пакета"
    word = plural_ru(n, "сообщество", "сообщества", "сообществ")
    lines = [f"Заказ{head_date}: {n} {word}, {money}"]
    for v in group:
        date = f"{v.publish_date:%d.%m %H:%M}" if v.publish_date else "—"
        status = STATUS_RU.get(v.status, v.status)
        kind = f" ({KIND_RU[v.kind]})" if v.kind in KIND_RU else ""
        photos = f" 📷{v.image_count}" if v.image_count else ""
        lines.append(f"• {v.region_name} — {date} — {status}{kind}{photos}")
        if v.vk_post_url:
            lines.append(f"  ↳ {v.vk_post_url}")
        elif v.status == "published":
            lines.append("  ↳ вышел, ссылка появится после сверки")
        if v.status == "rejected" and v.moderation_comment:
            lines.append(f"  ↳ причина: {v.moderation_comment[:120]}")
        if v.status == "failed" and v.error_message:
            lines.append(f"  ↳ ошибка: {v.error_message[:80]}")
    return "\n".join(lines)


def render(
    views: Sequence[PostView],
    *,
    hidden: int = 0,
    hidden_exact: bool = True,
    max_orders: int = 10,
    msg_max: int = MSG_MAX,
) -> List[str]:
    """Экран «Мои посты» — список сообщений под лимит ВК. Чистая.

    ``hidden`` — сколько заказов клиента не попало в выборку (считает
    ``list_for_client`` по всей истории, а не по окну).
    """
    if not views:
        return ["Постов пока нет — нажмите «🛒 Заказать пост»."]
    groups = _group(views)
    blocks = [render_group(g) for g in groups[:max_orders]]
    hidden = max(0, int(hidden)) + (len(groups) - len(blocks))
    if hidden > 0:
        word = plural_ru(hidden, "заказ", "заказа", "заказов")
        count = f"{hidden}" if hidden_exact else f"{hidden}+"
        blocks.append(f"…и ещё {count} {word} — полный список в кабинете: {CABINET_URL}")
    tail = f"\n…список обрезан, целиком — в кабинете: {CABINET_URL}"
    chunks: List[str] = []
    cur = HEAD
    for b in blocks:
        piece = "\n\n" + b
        if len(cur) + len(piece) <= msg_max:
            cur += piece
            continue
        # Шапку не отправляем отдельным пустым сообщением — она едет с блоком.
        prefix = HEAD + "\n\n" if cur == HEAD else ""
        if cur != HEAD:
            chunks.append(cur)
        room = msg_max - len(prefix)
        if len(b) <= room:
            cur = prefix + b
            continue
        # Блок сам длиннее сообщения: режем и честно говорим, что обрезали.
        cut = max(0, room - len(tail))
        chunks.append(prefix + b[:cut].rstrip() + tail)
        cur = HEAD if not prefix else ""
    if cur and cur != HEAD:
        chunks.append(cur)
    return chunks or [HEAD]


__all__ = [
    "PostView",
    "list_for_client",
    "order_key",
    "render",
    "render_group",
    "STATUS_RU",
    "KIND_RU",
]
