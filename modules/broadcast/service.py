"""Сетевая рассылка — сервисный слой (цели по умолчанию + хранилище картинок).

Чистая бизнес-логика, переиспользуемая API и диспетчером: резолв «всех пабликов
сети» (активные регионы с ``vk_group_id``, как у krugozor) и работа с папкой
загруженных картинок кампаний (CRUD из API, заливка из диспетчера).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_ALLOWED_IMG_EXT = (".jpg", ".jpeg", ".png")
MAX_IMG_BYTES = 12 * 1024 * 1024  # 12 МБ


def broadcast_image_dir() -> Path:
    """Папка загруженных картинок кампаний (создаётся при необходимости)."""
    d = Path(__file__).resolve().parents[2] / "web" / "static" / "broadcast"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_image_name(name: str) -> str:
    """Базовое имя без путей. Отсекает path-traversal и скрытые файлы."""
    base = Path(str(name or "")).name.strip()
    if not base or base.startswith("."):
        raise ValueError("Некорректное имя файла")
    if Path(base).suffix.lower() not in _ALLOWED_IMG_EXT:
        raise ValueError("Только JPG или PNG")
    return base


def broadcast_image_paths(names: Optional[List[str]] = None) -> List[Path]:
    """Пути к картинкам по именам (отфильтрованные, существующие, до 10)."""
    d = broadcast_image_dir()
    if not names:
        return []
    out: List[Path] = []
    for n in names:
        try:
            p = d / safe_image_name(n)
        except ValueError:
            continue
        if p.is_file():
            out.append(p)
    return out[:10]


async def default_targets(session: AsyncSession) -> List[Dict]:
    """Цели по умолчанию = все активные регионы сети с ``vk_group_id``.

    То же множество, что веер krugozor (16 пабликов на проде) — главные
    инфо-группы районов и областей, где у нас есть права постинга.
    Возвращает ``[{"group_id": int, "name": str}, …]``, отсортированные по имени.
    """
    from database.models import Region

    rows = (
        await session.execute(
            select(Region.vk_group_id, Region.name).where(
                Region.is_active.is_(True),
                Region.vk_group_id.isnot(None),
                Region.code != "copy",
            )
        )
    ).all()
    out = [{"group_id": int(gid), "name": name or ""} for gid, name in rows if gid is not None]
    out.sort(key=lambda x: x["name"].lower())
    return out
