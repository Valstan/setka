"""Save-архив радара (Ф0.4): скачивание медиа на диск + учёт квоты.

Раскладка: ``<RADAR_ARCHIVE_DIR>/<user_id>/<saved_id>/<NN>.<ext>``.
Дефолт каталога — ``/var/lib/setka/radar_archive`` (env ``RADAR_ARCHIVE_DIR``).

Квота (radar_users.quota_bytes) в Ф0 — предупредительная (решение владельца,
enforcement — Ф1): текст сохраняется всегда; медиа качаются, пока юзер
помещается в квоту, дальше остаются ссылками (entry без ``file``).

Box-level enforcement (Ф1): помимо per-user квоты ``download_media`` держит на
диске ≥ ``RADAR_ARCHIVE_MIN_FREE_BYTES`` свободно (защита всего 10-ГБ бокса:
поллер/Postgres/логи); суммарный потолок архива всех юзеров
(``RADAR_ARCHIVE_MAX_BYTES``) считает API и передаёт урезанный ``quota_left``.
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
# Telegram-CDN душит datacenter-egress (CF Worker) до ~0.2-1 КБ/с (замер
# деплоя Ф0.3: файл 31 КБ локально — 2с, через relay не успевает и за 120с).
# Скачивание TG-медиа де-факто best-effort: короткая попытка, не уложились —
# фото остаётся ссылкой (текст сохранён всегда, лента показывает CDN-URL
# напрямую в браузере юзера). Ф1-варианты: фоновое скачивание с ретраями /
# другой egress. Путь оставлен — на случай ослабления тарпита.
RELAY_DOWNLOAD_TIMEOUT_SECONDS = 20
MAX_FILE_BYTES = 20 * 1024 * 1024  # один файл больше 20 MB не качаем
MAX_FILES_PER_ITEM = 10

# Box-level enforcement квоты архива (Ф1, MANDATE brain 2026-06-13). Бокс setka —
# 10 ГБ; «вечный» архив без потолка переполнит диск и убьёт поллер/Postgres/логи.
DEFAULT_MIN_FREE_BYTES = 2 * 1024**3  # держим ≥2 ГиБ свободными на диске архива
DEFAULT_MAX_ARCHIVE_BYTES = 2 * 1024**3  # суммарный архив радара ≤2 ГиБ

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


_TG_CDN_RE = re.compile(
    r"^https://(?:[a-z0-9-]+\.)*(?:cdn-telegram\.org|telesco\.pe|telegram-cdn\.org)/", re.IGNORECASE
)


def _download_plan(url: str) -> Tuple[str, dict]:
    """(url, headers) для серверного скачивания: телеграмный CDN — через
    egress-relay с секретом (с VPS он заблокирован, probe Ф0); остальное —
    напрямую без доп. заголовков."""
    if _TG_CDN_RE.match(url):
        from modules.radar.sources.tg import relay_config, relay_media_url

        relayed = relay_media_url(url)
        if relayed:
            _, secret = relay_config()
            return relayed, {"X-Relay-Secret": secret}
    return url, {}


def archive_root() -> Path:
    return Path(os.getenv("RADAR_ARCHIVE_DIR", "/var/lib/setka/radar_archive"))


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("radar archive: bad %s=%r, using default %d", name, raw, default)
        return default


def min_free_bytes() -> int:
    """Минимум свободного места на ФС архива; ниже — медиа не качаем."""
    return _int_env("RADAR_ARCHIVE_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES)


def max_archive_bytes() -> int:
    """Глобальный потолок суммарного архива всех юзеров (enforce'ит API)."""
    return _int_env("RADAR_ARCHIVE_MAX_BYTES", DEFAULT_MAX_ARCHIVE_BYTES)


def disk_free_bytes() -> int:
    """Свободно байт на ФС каталога архива.

    Каталога ещё нет (не было сохранёнок) → берём ближайшего существующего
    предка. Сбой ``stat`` → большое число (fail-open: одиночный сбой не должен
    ронять сохранение; глобальный байт-потолок остаётся backstop'ом).
    """
    path = archive_root()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError as e:  # noqa: BLE001
        logger.warning("radar archive: disk_usage(%s) failed: %s", path, e)
        return 1 << 62


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
    min_free = min_free_bytes()

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for entry in (media or [])[:MAX_FILES_PER_ITEM]:
            new_entry = {"type": entry.get("type"), "url": entry.get("url")}
            if entry.get("type") == "photo" and entry.get("url"):
                try:
                    dl_url, dl_headers = _download_plan(entry["url"])
                    timeout = (
                        RELAY_DOWNLOAD_TIMEOUT_SECONDS if dl_headers else DOWNLOAD_TIMEOUT_SECONDS
                    )
                    response = await client.get(dl_url, headers=dl_headers, timeout=timeout)
                    response.raise_for_status()
                    blob = response.content
                    size = len(blob)
                    fits_size = 0 < size <= MAX_FILE_BYTES
                    fits_quota = downloaded + size <= quota_left
                    fits_disk = disk_free_bytes() - size >= min_free
                    if fits_size and fits_quota and fits_disk:
                        target.mkdir(parents=True, exist_ok=True)
                        ext = _ext_for(response.headers.get("content-type", ""), entry["url"])
                        filename = f"{file_no:02d}{ext}"
                        (target / filename).write_bytes(blob)
                        new_entry["file"] = filename
                        new_entry["bytes"] = size
                        downloaded += size
                        file_no += 1
                    elif fits_size and not fits_disk:
                        # Диск у порога — защищаем бокс: остаёмся ссылкой (Ф1).
                        logger.warning(
                            "radar archive: disk free below floor (%d B), photo kept as link: %s",
                            min_free,
                            entry["url"],
                        )
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
