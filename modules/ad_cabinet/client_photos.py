"""Библиотека фото рекламного клиента: файлы, лимиты, разбор вложений ВК (Этап 5).

Одна точка правды для кабинета (``POST /api/advertiser/photos``) и ВК-бота
(фото из вложений сообщения в личке САРАФАНа): каталог ``<root>/<client_id>/``,
допустимые расширения, вес, лимит файлов на клиента и пол свободного места.
Без FastAPI — модуль чистый, чтобы бот (modules-слой) не тянул ``web.api``.

Корень — ``AD_UPLOAD_DIR`` (прод: ``/var/lib/setka/ad_uploads``), иначе
``web/uploads/advertiser`` (разработка). Раскладка каталогов не меняется:
на неё завязаны ``photo_in_use``, ретенция (``photo_retention``), заливка фото
на стену (``_real_attachment_builder``) и owner-роут отдачи фото.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

logger = logging.getLogger(__name__)

ALLOWED_IMG_EXT = (".jpg", ".jpeg", ".png")
MAX_IMG_BYTES = 12 * 1024 * 1024  # 12 МБ — с запасом под прайс-PNG
MAX_PHOTOS_PER_POST = 10
MAX_PHOTOS_PER_CLIENT = 20
#: ВК отдаёт фото из сообщений как JPEG — под этим суффиксом бот и хранит.
BOT_PHOTO_SUFFIX = ".jpg"


class PhotoError(Exception):
    """Фото не принято. ``status`` — HTTP-код для кабинета (400/507), ``detail`` — текст клиенту."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail


def upload_root() -> Path:
    """Корень фото клиентов: ``AD_UPLOAD_DIR`` (прод — вне дерева репо) или
    ``web/uploads/advertiser`` (разработка). PR 1.8 аудита 2026-09-05."""
    from config.runtime import ad_upload_dir

    custom = ad_upload_dir()
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parents[2] / "web" / "uploads" / "advertiser"


def client_photo_dir(client_id: int) -> Path:
    d = upload_root() / str(int(client_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def fits_disk(size: int) -> bool:
    """Останется ли после записи ``size`` байт не меньше пола свободного места.

    Диск недоступен для замера → считаем, что влезает (как в архиве Радара):
    защита от переполнения, а не от сбоя statvfs.
    """
    from config.runtime import ad_upload_min_free_bytes

    try:
        free = shutil.disk_usage(upload_root()).free
    except OSError:
        return True
    return free - int(size) >= ad_upload_min_free_bytes()


def client_photo_paths(client_id: int) -> List[Path]:
    d = client_photo_dir(client_id)
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_IMG_EXT)


def store_client_photo(client_id: int, data: bytes, suffix: str) -> str:
    """Положить байты в библиотеку клиента → имя файла (``<uuid>.<ext>``).

    Порядок проверок и тексты — те же, что отдавал кабинет (400/507), чтобы
    клиент видел одно и то же и в кабинете, и в боте.
    """
    suffix = (suffix or "").lower()
    if suffix not in ALLOWED_IMG_EXT:
        raise PhotoError(400, "Только JPG/PNG")
    if not data:
        raise PhotoError(400, "Пустой файл")
    if len(data) > MAX_IMG_BYTES:
        raise PhotoError(400, "Файл больше 12 МБ")
    if len(client_photo_paths(client_id)) >= MAX_PHOTOS_PER_CLIENT:
        raise PhotoError(400, f"Лимит {MAX_PHOTOS_PER_CLIENT} фото — удалите лишние")
    if not fits_disk(len(data)):
        raise PhotoError(507, "На сервере мало места — напишите владельцу")
    name = f"{uuid.uuid4().hex}{suffix}"
    (client_photo_dir(client_id) / name).write_bytes(data)
    return name


def remove_client_photos(client_id: int, names: Sequence[str]) -> int:
    """Удалить перечисленные файлы клиента (только basename — из каталога не выйти).

    Отсутствующие и недоступные молча пропускаются; возвращает число удалённых.
    """
    d = client_photo_dir(client_id)
    removed = 0
    for n in names or []:
        base = Path(str(n)).name
        if not base:
            continue
        try:
            p = d / base
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            logger.debug("client photo %s/%s not removed", client_id, base, exc_info=True)
    return removed


def evict_oldest(client_id: int, keep: Set[str], count: int) -> List[str]:
    """Освободить место в библиотеке: удалить ``count`` самых старых файлов, не
    входящих в ``keep`` (фото активных постов и текущего черновика).

    Нужна клиентам только из бота: у них нет кабинета, чтобы «удалить лишние»,
    а вышедшие посты оставляют файлы до ретенции (180 дней). Возвращает имена
    удалённых.
    """
    if count <= 0:
        return []
    candidates = [p for p in client_photo_paths(client_id) if p.name not in keep]
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
    removed: List[str] = []
    for p in candidates[:count]:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            logger.debug("client photo %s/%s not evicted", client_id, p.name, exc_info=True)
    return removed


def photo_urls_from_attachments(
    attachments: Sequence[Dict[str, Any]], *, limit: int = MAX_PHOTOS_PER_POST
) -> List[str]:
    """Ссылки на самые крупные копии фото из вложений сообщения ВК. Чистая.

    Берём только ``type == "photo"`` с непустым ``sizes`` — максимум по ``width``;
    документы, видео и битые записи пропускаем. Ссылки подписанные и живут
    недолго — качать надо в том же тике, в состоянии хранить только имена файлов.
    """
    urls: List[str] = []
    for a in attachments or []:
        if not isinstance(a, dict) or a.get("type") != "photo":
            continue
        photo = a.get("photo") or {}
        sizes = [s for s in (photo.get("sizes") or []) if isinstance(s, dict) and s.get("url")]
        if not sizes:
            continue
        best = max(sizes, key=lambda s: int(s.get("width") or 0))
        urls.append(str(best["url"]))
        if len(urls) >= limit:
            break
    return urls


__all__ = [
    "ALLOWED_IMG_EXT",
    "MAX_IMG_BYTES",
    "MAX_PHOTOS_PER_POST",
    "MAX_PHOTOS_PER_CLIENT",
    "BOT_PHOTO_SUFFIX",
    "PhotoError",
    "upload_root",
    "client_photo_dir",
    "fits_disk",
    "client_photo_paths",
    "store_client_photo",
    "remove_client_photos",
    "evict_oldest",
    "photo_urls_from_attachments",
]
