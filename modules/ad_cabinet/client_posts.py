"""«📋 Мои посты» для ВК-бота: заказы клиента одним экраном (Этап 5, PR-3).

Чтение (``list_for_client``) отдельно от чистого рендера (``render``): бот и
любой другой канал показывают одно и то же. Группировка по ``order_ref`` (заказ
на N районов — один блок), имена районов, ссылка на вышедший пост из
``ad_publications`` (первична) либо по ``vk_postponed_post_id`` для
``published``; строки предложки/репостов (``kind``) помечаются. Длинный список
режется под лимит сообщения ВК и заканчивается ссылкой на кабинет.

``publish_date`` — МСК naive, печатается как есть; ``created_at`` — UTC naive,
в заголовке заказа сдвигается на +3 ч.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select

from database.models import AdPublication, AdScheduledPost, Region
from utils.text_utils import plural_ru

MSG_MAX = 4000  # запас до лимита ВК 4096 (как dialog.VK_MSG_MAX)
HEAD = "📋 Мои посты"
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


async def list_for_client(session, client_id: int, *, limit_rows: int = 60) -> List[PostView]:
    """Последние ``limit_rows`` строк отложки клиента (новые первыми) с именами
    районов и ссылками на вышедшие посты. Один запрос по постам, один — по
    публикациям."""
    rows = (
        await session.execute(
            select(AdScheduledPost, Region.name)
            .outerjoin(Region, Region.id == AdScheduledPost.region_id)
            .where(AdScheduledPost.client_id == int(client_id))
            .order_by(AdScheduledPost.id.desc())
            .limit(int(limit_rows))
        )
    ).all()
    ids = [int(r[0].id) for r in rows]
    pubs: Dict[int, AdPublication] = {}
    if ids:
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
    for post, region_name in rows:
        pub = pubs.get(int(post.id))
        url = None
        if pub is not None and pub.vk_post_id:
            url = f"https://vk.com/wall{pub.community_vk_id}_{pub.vk_post_id}"
        elif post.status == "published" and post.vk_postponed_post_id:
            url = f"https://vk.com/wall{post.community_vk_id}_{post.vk_postponed_post_id}"
        out.append(
            PostView(
                id=int(post.id),
                order_ref=post.order_ref,
                region_name=region_name or f"сообщество {post.community_vk_id}",
                community_vk_id=int(post.community_vk_id),
                publish_date=post.publish_date,
                status=str(post.status or ""),
                kind=str(getattr(post, "kind", None) or "post"),
                price=float(post.price or 0),
                moderation_comment=post.moderation_comment,
                error_message=post.error_message,
                image_count=len(post.image_names or []),
                vk_post_url=url,
                created_at=post.created_at,
            )
        )
    return out


def _money(v: float) -> str:
    return f"{float(v):,.0f} ₽".replace(",", " ")


def _group(views: Sequence[PostView]) -> List[List[PostView]]:
    """Группы по ``order_ref`` в порядке появления (список уже новые-первыми)."""
    groups: List[List[PostView]] = []
    index: Dict[str, int] = {}
    for v in views:
        key = v.order_ref or f"#{v.id}"
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


def render(views: Sequence[PostView], *, max_orders: int = 10, msg_max: int = MSG_MAX) -> List[str]:
    """Экран «Мои посты» — список сообщений под лимит ВК. Чистая."""
    if not views:
        return ["Постов пока нет — нажмите «🛒 Заказать пост»."]
    groups = _group(views)
    blocks = [render_group(g) for g in groups[:max_orders]]
    hidden = len(groups) - len(blocks)
    if hidden > 0:
        word = plural_ru(hidden, "заказ", "заказа", "заказов")
        blocks.append(f"…и ещё {hidden} {word} — полный список в кабинете: {CABINET_URL}")
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


__all__ = ["PostView", "list_for_client", "render", "render_group", "STATUS_RU", "KIND_RU"]
