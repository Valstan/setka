"""RSS/Atom-адаптер радара (Ф0.2).

Фетч — httpx (уже в зависимостях), парсинг — feedparser (новая зависимость,
план Ф0). feedparser синхронный, но парсит уже скачанные байты — event loop
не блокируется на сети; парсинг типичного фида — миллисекунды.

``source.key`` — канонизированный URL фида.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import feedparser
import httpx

from modules.radar.sources import FetchedItem

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
MAX_ENTRIES = 50
USER_AGENT = "SETKA-Radar/1.0 (+https://github.com/Valstan/setka)"


def _entry_published(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None) or entry.get(attr)
        if parsed:
            try:
                return datetime(*parsed[:6])
            except (TypeError, ValueError):
                continue
    return None


def _entry_external_id(entry) -> Optional[str]:
    # guid → link → title: стабильный id важнее красоты.
    for key in ("id", "link", "title"):
        value = entry.get(key)
        if value:
            return str(value)[:256]
    return None


def parse_feed_bytes(raw: bytes) -> List[FetchedItem]:
    """Распарсить байты фида в нормализованные элементы (отделено для тестов)."""
    parsed = feedparser.parse(raw)
    items: List[FetchedItem] = []
    for entry in parsed.entries[:MAX_ENTRIES]:
        external_id = _entry_external_id(entry)
        if not external_id:
            continue
        summary = entry.get("summary") or ""
        items.append(
            FetchedItem(
                external_id=external_id,
                url=entry.get("link"),
                title=(entry.get("title") or "").strip()[:512] or None,
                text=summary.strip() or None,
                published_at=_entry_published(entry),
            )
        )
    return items


async def fetch_new(source) -> List[FetchedItem]:
    """Скачать фид ``source.key`` и вернуть его элементы.

    Сетевые/HTTP-ошибки пробрасываются — поллер по ним инкрементит fail_count.
    """
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await client.get(source.key)
        response.raise_for_status()
    return parse_feed_bytes(response.content)
