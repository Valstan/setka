"""Source-адаптеры контент-радара (Ф0.2).

Контракт: ``fetch_new(source) -> list[FetchedItem]`` — асинхронно забрать
свежие элементы источника. Адаптер НЕ знает про БД/дедуп: возвращает всё,
что отдал источник за окно, дедуп делает поллер через uniq
(source_id, external_id) в ``radar_items``.

Регистрация по ``source.type``: vk|rss (Ф0.2), tg — Ф0.3 через egress-relay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, List, Optional


@dataclass
class FetchedItem:
    """Нормализованный элемент из любого источника."""

    external_id: str
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    media: List[dict] = field(default_factory=list)  # [{type, url}]
    published_at: Optional[datetime] = None


FetcherFn = Callable[[object], Awaitable[List[FetchedItem]]]


def get_fetcher(source_type: str) -> Optional[FetcherFn]:
    """Адаптер по типу источника; None — тип не поддержан (tg до Ф0.3)."""
    # Импорты внутри — лёгкий старт тестов и отсутствие циклов.
    if source_type == "vk":
        from modules.radar.sources.vk import fetch_new

        return fetch_new
    if source_type == "rss":
        from modules.radar.sources.rss import fetch_new

        return fetch_new
    return None
