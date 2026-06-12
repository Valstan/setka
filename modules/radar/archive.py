"""Save-архив радара (Ф0.4): скачивание медиа на диск + учёт квоты.

Раскладка: ``<RADAR_ARCHIVE_DIR>/<user_id>/<saved_id>/<NN>.<ext>``.
Дефолт каталога — ``/var/lib/setka/radar_archive`` (env ``RADAR_ARCHIVE_DIR``).

Квота (radar_users.quota_bytes) в Ф0 — предупредительная (решение владельца,
enforcement — Ф1): текст сохраняется всегда; медиа качаются, пока юзер
помещается в квоту, дальше остаются ссылками (entry без ``file``).
Видео — всегда ссылкой (решение владельца).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_FILE_BYTES = 20 * 1024 * 1024  # один файл больше 20 MB не качаем
MAX_FILES_PER_ITEM = 10

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def archive_root() -> Path:
    return Path(os.getenv("RADAR_ARCHIVE_DIR", "/var/lib/setka/radar_archive"))


def saved_dir(user_id: int, saved_id: int) -> Path:
    return archive_root() / str(user_id) / str(saved_id)


def _ext_for(content_type: str, url: str) -> str:
    ext = _EXT_BY_CONTENT_TYPE.get((content_type or "").split(";")[0].strip())
    if ext:
        return ext
    match = re.search(r"\.(jpe?g|png|webp|gif)(?:\?|$)", url, re.IGNORECASE)
    return f".{match.group(1).lower()}" if match else ".bin"


async def download_media(
    media: List[dict],
    user_id: int,
    saved_id: int,
    *,
    quota_left: int,
) -> Tuple[List[dict], int]:
    """Скачать фото из ``media`` в каталог сохранёнки.

    Возвращает (новый media-список, скачано байт). Фото, не влезшее в квоту /
    упавшее при скачивании, остаётся ссылкой (entry без ``file``) — сохранёнка
    не ломается. Видео не качаем by design.
    """
    result: List[dict] = []
    downloaded = 0
    target = saved_dir(user_id, saved_id)
    file_no = 0

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for entry in (media or [])[:MAX_FILES_PER_ITEM]:
            new_entry = {"type": entry.get("type"), "url": entry.get("url")}
            if entry.get("type") == "photo" and entry.get("url"):
                try:
                    response = await client.get(entry["url"])
                    response.raise_for_status()
                    blob = response.content
                    if 0 < len(blob) <= MAX_FILE_BYTES and downloaded + len(blob) <= quota_left:
                        target.mkdir(parents=True, exist_ok=True)
                        ext = _ext_for(response.headers.get("content-type", ""), entry["url"])
                        filename = f"{file_no:02d}{ext}"
                        (target / filename).write_bytes(blob)
                        new_entry["file"] = filename
                        new_entry["bytes"] = len(blob)
                        downloaded += len(blob)
                        file_no += 1
                except Exception as e:  # noqa: BLE001 - остаёмся ссылкой
                    logger.warning("radar archive: photo download failed (%s): %s", entry["url"], e)
            result.append(new_entry)

    return result, downloaded


def media_file_path(user_id: int, saved_id: int, filename: str) -> Optional[Path]:
    """Безопасный путь к файлу сохранёнки; None при traversal/отсутствии."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    path = saved_dir(user_id, saved_id) / filename
    return path if path.is_file() else None


def remove_saved_dir(user_id: int, saved_id: int) -> None:
    """Удалить каталог сохранёнки с диска (best-effort)."""
    try:
        shutil.rmtree(saved_dir(user_id, saved_id), ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("radar archive: rmtree failed for %s/%s: %s", user_id, saved_id, e)
