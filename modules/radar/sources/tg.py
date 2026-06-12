"""TG-адаптер радара через egress-relay (Ф0.3).

С прод-VPS заблокирован весь Telegram кроме api.telegram.org (probe Ф0,
PR #196) → ходим через Cloudflare Worker (infra/tg_relay/worker.js):
он проксирует AJAX-вариант ``t.me/s/<channel>`` (полная лента; обычный GET
для datacenter-IP деградирован до 1 сообщения — факт деплоя Ф0.3) и
медиа-CDN (``/media?u=``). Доступ к relay — по секрету (#008):
env ``TG_PREVIEW_RELAY_URL`` + ``TG_RELAY_SECRET``.

Парсинг — поверх probe-доказанных селекторов (scripts/probe_tme_s_parsing.py):
``data-post``, ``<time datetime>``, photo/video классы, redirect = мёртвый
канал. Ответ relay — JSON-строка с HTML-фрагментом (AJAX-формат t.me).

``source.key`` — username канала (без @).
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

import httpx

from modules.radar.sources import FetchedItem

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")

_MSG_SPLIT_RE = re.compile(r'data-post="(?P<ch>[^/"]+)/(?P<id>\d+)"')
_DATE_RE = re.compile(r'<time datetime="([^"]+)"')
_PHOTO_RE = re.compile(
    r"tgme_widget_message_photo_wrap[^\"']*[\"'][^>]*background-image:url\('([^']+)'\)"
)
_VIDEO_RE = re.compile(r"tgme_widget_message_video_player|tgme_widget_message_video_wrap")
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')


def relay_config() -> Tuple[str, str]:
    """(base_url, secret) из env; RuntimeError если relay не сконфигурирован."""
    base = (os.getenv("TG_PREVIEW_RELAY_URL") or "").rstrip("/")
    secret = os.getenv("TG_RELAY_SECRET") or ""
    if not base or not secret:
        raise RuntimeError("TG relay is not configured (TG_PREVIEW_RELAY_URL/TG_RELAY_SECRET)")
    return base, secret


def relay_media_url(original_url: str) -> Optional[str]:
    """URL медиа через relay (для серверного скачивания в архив); None без relay."""
    try:
        base, _ = relay_config()
    except RuntimeError:
        return None
    from urllib.parse import quote

    return f"{base}/media?u={quote(original_url, safe='')}"


def parse_channel_value(value: str) -> str:
    """Сырой ввод ('@ch', 't.me/ch', 'https://t.me/s/ch') → username канала."""
    v = value.strip()
    for prefix in ("https://", "http://"):
        if v.startswith(prefix):
            v = v[len(prefix) :]
    for host in ("t.me/s/", "t.me/", "telegram.me/"):
        if v.startswith(host):
            v = v[len(host) :]
    v = v.lstrip("@").split("?")[0].split("/")[0].strip()
    if not CHANNEL_RE.match(v):
        raise ValueError(f"Не похоже на Telegram-канал: '{value}'")
    return v


def _unwrap_body(body: str) -> str:
    """Relay отдаёт AJAX-ответ t.me — JSON-строку с HTML; GET-вариант — HTML."""
    if body.startswith('"'):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = _TAG_RE.sub("", text)
    return html_lib.unescape(text).strip()


def parse_messages(page_html: str) -> List[dict]:
    """HTML ленты → [{id, channel, text, photos, has_video, published_at}].

    Режем страницу по якорям ``data-post`` — всё до следующего якоря
    относится к текущему сообщению (вёрстка t.me, probe-доказано).
    """
    anchors = list(_MSG_SPLIT_RE.finditer(page_html))
    messages: List[dict] = []
    seen_ids = set()
    for i, match in enumerate(anchors):
        msg_id = int(match.group("id"))
        if msg_id in seen_ids:  # grouped-media дублирует data-post
            continue
        seen_ids.add(msg_id)
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(page_html)
        block = page_html[match.start() : end]

        text_match = _TEXT_RE.search(block)
        date_match = _DATE_RE.search(block)
        published = None
        if date_match:
            try:
                published = datetime.fromisoformat(date_match.group(1)).replace(tzinfo=None)
            except ValueError:
                pass
        messages.append(
            {
                "id": msg_id,
                "channel": match.group("ch"),
                "text": _strip_html(text_match.group(1)) if text_match else None,
                "photos": _PHOTO_RE.findall(block),
                "has_video": bool(_VIDEO_RE.search(block)),
                "published_at": published,
            }
        )
    return messages


async def _relay_fetch_page(
    channel: str, before: Optional[int] = None
) -> Tuple[str, Optional[str]]:
    """Страница ленты через relay → (html, redirect_location|None)."""
    base, secret = relay_config()
    params = {"before": str(before)} if before else None
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{base}/s/{channel}", params=params, headers={"X-Relay-Secret": secret}
        )
    redirect = response.headers.get("x-relay-redirect")
    if response.status_code in (301, 302) or redirect:
        return "", redirect or "?"
    response.raise_for_status()
    return _unwrap_body(response.text), None


def _to_items(channel: str, messages: List[dict]) -> List[FetchedItem]:
    items: List[FetchedItem] = []
    for msg in messages:
        media = [{"type": "photo", "url": u} for u in msg["photos"]]
        if msg["has_video"]:
            media.append({"type": "video", "url": f"https://t.me/{channel}/{msg['id']}"})
        items.append(
            FetchedItem(
                external_id=str(msg["id"]),
                url=f"https://t.me/{channel}/{msg['id']}",
                text=msg["text"],
                media=media,
                published_at=msg["published_at"],
            )
        )
    return items


async def fetch_new(source) -> List[FetchedItem]:
    """Свежие сообщения канала ``source.key`` через relay.

    Redirect = канал умер/закрылся → исключение (поллер инкрементит
    fail_count). Telegram отдаёт datacenter-IP неполную глубину (3-5 на
    страницу) — для поллинга раз в 10 минут достаточно, дедуп на БД.
    """
    channel = source.key
    page, redirect = await _relay_fetch_page(channel)
    if redirect is not None:
        raise RuntimeError(f"t.me/s/{channel} redirects ({redirect}): канал без web-превью")
    return _to_items(channel, parse_messages(page))


async def resolve_source(value: str) -> dict:
    """Сырой ввод → {key, title, url}; ValueError если канал не живёт в превью."""
    channel = parse_channel_value(value)
    try:
        page, redirect = await _relay_fetch_page(channel)
    except RuntimeError:
        raise  # relay не сконфигурирован — отдаём как есть (API превратит в 503)
    except Exception as e:  # noqa: BLE001 - сеть → человекочитаемая причина
        raise ValueError(f"Канал недоступен через relay: {e}") from e
    if redirect is not None:
        raise ValueError(f"У t.me/{channel} нет web-превью (канал приватный или не существует)")

    title_match = _TITLE_RE.search(page)
    title = html_lib.unescape(title_match.group(1)) if title_match else None
    if not title:
        # AJAX-фрагмент без <head> — берём имя автора из первого сообщения.
        owner = re.search(
            r'class="tgme_widget_message_owner_name"[^>]*>(?:<span[^>]*>)?([^<]+)', page
        )
        title = html_lib.unescape(owner.group(1)).strip() if owner else None
    return {"key": channel, "title": title or f"@{channel}", "url": f"https://t.me/{channel}"}
