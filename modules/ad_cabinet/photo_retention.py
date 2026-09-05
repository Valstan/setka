"""Ретенция фото рекламных клиентов (PR 1.8 аудита кабинета 2026-09-05).

Фото живут файлами вне БД (``web/api/advertiser_cabinet._client_photo_dir``),
поэтому ничего не удаляет их само: архивный клиент оставлял каталог навсегда,
а библиотека живого клиента росла без предела на 10-ГБ боксе. Правила:

- клиент **в архиве** (``ad_clients.is_archived``) — каталог удаляется целиком;
- у живого клиента удаляются файлы старше ``keep_days`` (дефолт 180), на
  которые **не ссылается** ни один активный пост (``image_names`` строк
  ``ad_scheduled_posts`` в статусах pending/draft/scheduled).

Чистая логика с инъекцией каталога и «сейчас» — гоняется на ``tmp_path``.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy import select

from database.models import AdClient, AdScheduledPost

logger = logging.getLogger(__name__)

KEEP_DAYS = int(os.getenv("AD_PHOTO_KEEP_DAYS", "180"))
ACTIVE_STATUSES = ("pending", "draft", "scheduled")


async def referenced_names(session, client_id: int) -> Set[str]:
    """Имена файлов, на которые ссылаются активные посты клиента."""
    rows = (
        (
            await session.execute(
                select(AdScheduledPost.image_names).where(
                    AdScheduledPost.client_id == int(client_id),
                    AdScheduledPost.status.in_(ACTIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    out: Set[str] = set()
    for names in rows:
        out.update(str(n) for n in (names or []))
    return out


async def run_photo_retention(
    *,
    session_factory: Optional[Callable] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    keep_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Пройти каталоги ``<root>/<client_id>/`` и применить правила."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    if root is None:
        from web.api.advertiser_cabinet import _upload_root

        root = _upload_root()
    now = now or datetime.utcnow()
    keep_days = KEEP_DAYS if keep_days is None else keep_days
    cutoff = (now - timedelta(days=keep_days)).timestamp()

    stats = {"dirs": 0, "archived_removed": 0, "files_removed": 0, "bytes_freed": 0}
    root = Path(root)
    if not root.is_dir():
        return stats

    async with session_factory() as session:
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                client_id = int(d.name)
            except ValueError:
                continue
            stats["dirs"] += 1
            client = await session.get(AdClient, client_id)
            if client is None or client.is_archived:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
                stats["archived_removed"] += 1
                stats["bytes_freed"] += size
                continue
            keep = await referenced_names(session, client_id)
            for f in d.iterdir():
                if not f.is_file() or f.name in keep:
                    continue
                if f.stat().st_mtime < cutoff:
                    size = f.stat().st_size
                    try:
                        f.unlink()
                    except OSError as e:  # pragma: no cover - защита
                        logger.warning("photo retention: unlink %s failed: %s", f, e)
                        continue
                    stats["files_removed"] += 1
                    stats["bytes_freed"] += size
    if stats["archived_removed"] or stats["files_removed"]:
        logger.info("ad photo retention: %s", stats)
    return stats


__all__ = ["run_photo_retention", "referenced_names", "KEEP_DAYS", "ACTIVE_STATUSES"]
