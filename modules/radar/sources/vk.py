"""VK-адаптер радара: тонкая обёртка над готовым wall.get-стеком (Ф0.2).

``source.key`` — owner_id стены строкой (отрицательный для сообществ).
Токен — parse-токен VALSTAN из env (как у notification-задач); community-токены
не нужны: радар читает только публичные стены.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from modules.radar.sources import FetchedItem

logger = logging.getLogger(__name__)

# Сколько постов забираем за поллинг. Окно поллера ~10 мин — даже у
# плодовитой стены 20 хватает с большим запасом; дедуп всё равно на БД.
FETCH_COUNT = 20


def _parse_vk_value(value: str) -> str:
    """Сырой ввод юзера → screen_name либо численный owner_id строкой.

    Принимает: '-218688001', 'club218688001', 'public218688001', 'id123',
    'gonba_life', 'https://vk.com/gonba_life', 'vk.com/club218688001'.
    """
    v = value.strip()
    for prefix in ("https://", "http://"):
        if v.startswith(prefix):
            v = v[len(prefix) :]
    for host in ("m.vk.com/", "vk.com/", "vk.ru/"):
        if v.startswith(host):
            v = v[len(host) :]
    v = v.split("?")[0].split("/")[0].strip()
    if not v:
        raise ValueError("empty VK source")
    if v.lstrip("-").isdigit():
        return v
    for prefix, sign in (("club", -1), ("public", -1), ("event", -1), ("id", 1)):
        tail = v[len(prefix) :]
        if v.startswith(prefix) and tail.isdigit():
            return str(sign * int(tail))
    return v  # screen_name — резолвим через VK API


async def resolve_source(value: str) -> dict:
    """Сырой ввод → {key, title, url} для создания radar_source типа vk.

    key — owner_id стены строкой (отрицательный для сообществ). Бросает
    ValueError, если источник не находится в VK.
    """
    from config.runtime import VK_TOKENS
    from modules.vk_monitor.vk_client_async import VKClientAsync

    token = VK_TOKENS.get("VALSTAN")
    if not token:
        raise RuntimeError("VK token VALSTAN is not configured")

    parsed = _parse_vk_value(value)
    async with VKClientAsync(token) as client:
        if not parsed.lstrip("-").isdigit():
            resolved = await client._make_request(
                "utils.resolveScreenName", {"screen_name": parsed}
            )
            if not resolved or not resolved.get("object_id"):
                raise ValueError(f"VK не знает '{parsed}'")
            object_id = int(resolved["object_id"])
            owner_id = (
                -object_id if resolved.get("type") in ("group", "page", "event") else object_id
            )
        else:
            owner_id = int(parsed)

        title = None
        if owner_id < 0:
            info = await client.get_group_info(owner_id)
            if info is None:
                raise ValueError(f"Сообщество {owner_id} не найдено в VK")
            title = info.get("name")
            screen = info.get("screen_name")
            url = f"https://vk.com/{screen}" if screen else f"https://vk.com/club{-owner_id}"
        else:
            url = f"https://vk.com/id{owner_id}"

    return {"key": str(owner_id), "title": title, "url": url}


def _media_from_attachments(post: dict) -> List[dict]:
    """Превью-метаданные вложений: фото (max size url) и видео (ссылкой)."""
    media: List[dict] = []
    for att in post.get("attachments") or []:
        att_type = att.get("type")
        if att_type == "photo":
            sizes = (att.get("photo") or {}).get("sizes") or []
            if sizes:
                best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                if best.get("url"):
                    media.append({"type": "photo", "url": best["url"]})
        elif att_type == "video":
            video = att.get("video") or {}
            owner, vid = video.get("owner_id"), video.get("id")
            if owner is not None and vid is not None:
                media.append({"type": "video", "url": f"https://vk.com/video{owner}_{vid}"})
    return media


async def fetch_new(source) -> List[FetchedItem]:
    """Свежие посты стены ``source.key`` → нормализованные элементы.

    Ошибки VK не глотаем молча на уровне адаптера выше get_wall_posts (он сам
    возвращает [] при VKAPIException) — пустой список при живом источнике
    безопасен: поллер не двигает fail_count при отсутствии исключения, а
    реальный обрыв сети поднимет исключение из aiohttp до поллера.
    """
    from config.runtime import VK_TOKENS
    from modules.vk_monitor.vk_client_async import VKClientAsync

    token = VK_TOKENS.get("VALSTAN")
    if not token:
        raise RuntimeError("VK token VALSTAN is not configured")

    owner_id = int(source.key)
    async with VKClientAsync(token) as client:
        posts = await client.get_wall_posts(owner_id, count=FETCH_COUNT)

    items: List[FetchedItem] = []
    for post in posts:
        post_id = post.get("id")
        if post_id is None:
            continue
        # Закреп может быть старым — он и так отсеется дедупом; не фильтруем.
        items.append(
            FetchedItem(
                external_id=str(post_id),
                url=f"https://vk.com/wall{owner_id}_{post_id}",
                text=(post.get("text") or "").strip() or None,
                media=_media_from_attachments(post),
                published_at=(
                    datetime.utcfromtimestamp(post["date"]) if post.get("date") else None
                ),
            )
        )
    return items
